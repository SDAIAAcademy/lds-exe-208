"""Shared code of the LDS-EXE-208 harness: items, scoring rules, model calls, the scripted
stand-in, statistics and the run log. Standard library only.

Scoring rules (the course's own, stated on the lab page):
  number  the first number in the answer equals the reference
  label   the answer, as words, holds the reference label and none of the other labels
  exact   the answer holds the reference string (letters and digits, case ignored)
  terms   the answer holds every required term
Arabic text is normalised first: diacritics and tatweel removed, alef forms to bare alef,
taa marbuta to haa, alef maqsura to yaa, Arabic-Indic digits to Western digits, Latin lower case.
"""
import csv
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABS = HERE.parent
FILES = LABS / 'files'
RUNS = Path(os.environ['LDS208_RUNS']) if os.environ.get('LDS208_RUNS') else HERE / 'runs'
VERSION = '1.0'
SAR_PER_USD = 3.75
NOT_FILLED = 'not filled'

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def rel(path):
    """A path for messages and logs: relative to labs/, never absolute."""
    try:
        return Path(path).resolve().relative_to(LABS).as_posix()
    except ValueError:
        return Path(path).name


def items():
    return read_csv(FILES / 'items.csv')


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ------------------------------------------------------------------ normalising and scoring
DIACRITICS = re.compile('[ً-ْٰـ]')
AR_DIGITS = str.maketrans('٠١٢٣٤٥٦٧٨٩',
                          '0123456789')


def norm(text):
    t = DIACRITICS.sub('', text or '')
    t = re.sub('[آأإ]', 'ا', t)
    t = t.replace('ة', 'ه').replace('ى', 'ي')
    return t.translate(AR_DIGITS).lower()


def words(text):
    return re.findall(r'[0-9a-zء-ي]+', norm(text))


def first_number(text):
    m = re.search(r'-?\d[\d,]*(\.\d+)?', norm(text))
    return None if not m else float(m.group(0).replace(',', ''))


def score(item, answer):
    check, ref = item['check'], item['reference']
    if answer is None:
        return False
    if check == 'number':
        n = first_number(answer)
        return n is not None and abs(n - float(ref)) < 1e-9
    if check == 'label':
        labels = [norm(x) for x in item['accepted'].split('|')]
        got = set(words(answer))
        target = norm(ref)
        return target in got and not any(l in got for l in labels if l != target)
    if check == 'exact':
        squash = lambda s: re.sub(r'[^0-9a-z]', '', norm(s))
        return squash(ref) in squash(answer)
    if check == 'terms':
        a = ' '.join(words(answer))
        return all(' '.join(words(t)) in a for t in item['accepted'].split('|'))
    raise ValueError('unknown check ' + check)


# ------------------------------------------------------------------ statistics
def median(xs):
    s = sorted(xs)
    if not s:
        return None
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def p90(xs):
    """90th percentile by the nearest-rank rule: the value at rank ceil(0.9 n)."""
    s = sorted(xs)
    if not s:
        return None
    k = -(-9 * len(s) // 10)
    return s[k - 1]


# ------------------------------------------------------------------ configuration
SYSTEM = {
    'ar': 'أنت تجيب عن أسئلة من نصوص منشورة لأمانة مدينة الكثيب. أجب بإيجاز وبالعربية، والتزم بصيغة الإجابة المطلوبة في السؤال.',
    'en': 'You answer questions from published texts of Al-Kathib City Municipality. Answer briefly in English and keep to the answer format the question asks for.',
}


def load_config(path):
    path = Path(path)
    if not path.exists():
        sys.exit(f'no configuration at {rel(path)}: copy harness/config.example.json to harness/config.json and fill it, '
                 'or add --standin to run the scripted stand-in')
    try:
        cfg = json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        sys.exit(f'{rel(path)} is not valid JSON (line {e.lineno}): fix it and run again')
    missing = [f'{side}.{k}' for side in ('hosted', 'local') for k in ('base_url', 'model')
               if not str(cfg.get(side, {}).get(k, '')).strip()]
    if missing:
        sys.exit('fill these in ' + rel(path) + ' first: ' + ', '.join(missing) +
                 ' (or add --standin to run the scripted stand-in)')
    return cfg


def money(value):
    v = str(value if value is not None else '').strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def data_path(side_cfg):
    host = re.sub(r'^https?://', '', side_cfg.get('base_url', '')).split('/')[0].split(':')[0]
    stays = host in ('localhost', '127.0.0.1', '[::1]') or host.endswith('.localhost')
    return {
        'host': host or NOT_FILLED,
        'leaves_device': not stays,
        'where_it_runs': side_cfg.get('where') or NOT_FILLED,
        'data_use_on_this_tier': side_cfg.get('data_use_on_this_tier') or NOT_FILLED,
    }


def standin_config():
    prices = read_csv(FILES / 'price_sheet.csv')
    inc = [p for p in prices if p['model'] == 'orv-large-3' and p['registered_with_cst'] == 'yes'][0]
    return {
        'hosted': {'label': 'stand-in for a hosted closed model (scripted)', 'base_url': 'https://standin.invalid/v1',
                   'model': 'standin-hosted', 'where': 'scripted: no request is sent',
                   'data_use_on_this_tier': 'scripted: no request is sent',
                   'input_usd_per_million': inc['input_usd_per_million_tokens'],
                   'output_usd_per_million': inc['output_usd_per_million_tokens']},
        'local': {'label': 'stand-in for a local open-weight model (scripted)', 'base_url': 'http://localhost/standin',
                  'model': 'standin-local', 'where': 'scripted: no request is sent',
                  'data_use_on_this_tier': 'scripted: no request is sent',
                  'server_sar_per_month': '1500', 'parallel_slots': '1'},
    }


# ------------------------------------------------------------------ calling a model
class CallFailed(Exception):
    pass


def call_model(side_cfg, lang, prompt, timeout=120):
    key_env = side_cfg.get('api_key_env') or ''
    key = os.environ.get(key_env, '') if key_env else ''
    if key_env and not key:
        raise CallFailed(f'the environment variable {key_env} is empty')
    body = json.dumps({'model': side_cfg['model'], 'temperature': 0,
                       'messages': [{'role': 'system', 'content': SYSTEM[lang]},
                                    {'role': 'user', 'content': prompt}]}).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    url = side_cfg['base_url'].rstrip('/') + '/chat/completions'
    req = urllib.request.Request(url, data=body, headers=headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise CallFailed(f'HTTP {e.code}')
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CallFailed(f'no answer from {data_path(side_cfg)["host"]} ({type(e).__name__}): is the runner started?')
    except json.JSONDecodeError:
        raise CallFailed('the answer was not JSON')
    ms = round((time.perf_counter() - t0) * 1000)
    try:
        text = data['choices'][0]['message']['content'].strip()
    except (KeyError, IndexError, TypeError, AttributeError):
        raise CallFailed('the answer had no message')
    usage = data.get('usage') or {}
    if usage.get('prompt_tokens') is not None:
        return text, ms, usage.get('prompt_tokens'), usage.get('completion_tokens'), 'usage'
    return text, ms, est_tokens(SYSTEM[lang] + prompt), est_tokens(text), 'estimated'


def est_tokens(text):
    return max(1, round(len(text) / 4))


def standin_answers():
    return {(r['side'], r['id']): (r['answer'], int(r['latency_ms'])) for r in read_csv(HERE / 'standin.csv')}


# ------------------------------------------------------------------ cost
def item_cost_usd(side_cfg, tin, tout):
    pin, pout = money(side_cfg.get('input_usd_per_million')), money(side_cfg.get('output_usd_per_million'))
    if pin is None or pout is None:
        return None
    return (tin * pin + tout * pout) / 1e6


# ------------------------------------------------------------------ run log
def new_run_id(mode, label):
    seed = f'{mode}|{label}|{time.time_ns() if mode == "model" else 0}'
    return label + '-' + hashlib.sha256(seed.encode()).hexdigest()[:8]


def today(mode):
    return time.strftime('%Y-%m-%d') if mode == 'model' else 'standin'


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')


def read_jsonl(path):
    with open(path, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


def summarise(rows, side_cfg, lang_items):
    """rows: item records of one side. Returns the numbers of the results sheet."""
    out = {}
    for lang in ('ar', 'en', 'all'):
        # An item that came back with an error is counted under errors, not as a quality failure
        rs = [r for r in rows if (lang == 'all' or r['lang'] == lang) and not r.get('error')]
        out[f'pass_{lang}'] = sum(1 for r in rs if r['pass'])
        out[f'n_{lang}'] = len(rs)
    lat = [r['latency_ms'] for r in rows if r['latency_ms'] is not None]
    out['median_ms'] = median(lat)
    out['p90_ms'] = p90(lat)
    costs = [r['cost_usd'] for r in rows if not r.get('error')]
    out['cost_usd_per_1000'] = None if any(c is None for c in costs) or not costs else round(1000 * sum(costs) / len(costs), 4)
    out['failed_items'] = ' '.join(r['item'] for r in rows if not r['pass'] and not r.get('error'))
    out['errors'] = sum(1 for r in rows if r.get('error'))
    return out


def fmt(v, unit='', none=NOT_FILLED):
    if v is None:
        return none
    if isinstance(v, float):
        v = round(v, 4)
    return f'{v}{unit}'
