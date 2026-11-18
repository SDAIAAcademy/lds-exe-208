# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Trainer's check of the labs of LDS-EXE-208. From the repository root:

    uv run labs/solutions/check_all.py                       # every check; prints PASS or each difference
    uv run labs/solutions/check_all.py --placement <file>    # score one participant's placement table (lab 1.1)

1. The generator writes the same bytes as the case files on disk.
2. The placement key uses only the codes of the scheme, and scores 50 of 50 against itself;
   the empty template scores 0 without an error.
3. The stand-in runs of labs 1.2 and 1.3 give the numbers in key.json, and verify_log.py agrees
   with every verdict in their logs.
4. The starting state does not crash: no configuration, no log, an unknown scenario each end
   with a message, not a traceback.
Runs go to a temporary folder that is deleted afterwards.
"""
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SOL = Path(__file__).resolve().parent
LABS = SOL.parent
KEY = json.loads((SOL / 'key.json').read_text(encoding='utf-8'))
fails = 0
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def read_csv(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def diff(msg):
    global fails
    fails += 1
    print('DIFF ' + msg)


def eq(name, got, want):
    if got != want:
        diff(f'{name}: got {got!r}, want {want!r}')


def run(args, env=None):
    e = dict(os.environ, PYTHONIOENCODING='utf-8', **(env or {}))
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, encoding='utf-8', env=e, cwd=LABS)


# ---------------------------------------------------------------- placement scoring (lab 1.1)
def score_placement(path):
    """Five points a row: class; personal data and basis; kind; widest placement; deciding rule."""
    key = {r['id']: r for r in read_csv(SOL / 'key_placement.csv')}
    got = {r.get('id', '').strip(): r for r in read_csv(path)}
    total, lines = 0, []
    for wid, k in key.items():
        row = got.get(wid, {})
        g = {c: (row.get(c) or '').strip() for c in k}
        g['deciding_rule'] = (row.get('deciding_rule') or (row.get('accepted_rules') or '').split('|')[0]).strip()
        pts = [g['data_class'] == k['data_class'],
               g['personal_data'] == k['personal_data'] and g['lawful_basis'] == k['lawful_basis'],
               g['allowed_kind'] == k['allowed_kind'],
               g['widest_placement'].upper() == k['widest_placement'],
               g.get('deciding_rule', '').upper() in k['accepted_rules'].split('|')]
        total += sum(pts)
        lines.append(f'{wid}: {sum(pts)}/5' + ('' if all(pts) else '  (missed: ' + ', '.join(
            n for n, p in zip(['class', 'personal data and basis', 'kind', 'placement', 'rule'], pts) if not p) + ')'))
    return total, lines


def check_placement_key():
    scheme = (LABS / 'files' / 'placement_scheme.md').read_text(encoding='utf-8')
    for r in read_csv(SOL / 'key_placement.csv'):
        eq(r['id'] + ' class', r['data_class'] in ('عام', 'مقيد', 'سري', 'سري للغاية'), True)
        eq(r['id'] + ' kind', r['allowed_kind'] in ('generative', 'non_generative', 'none'), True)
        eq(r['id'] + ' placement', r['widest_placement'] in ('L1', 'L2', 'L3', 'L4'), True)
        for rule in r['accepted_rules'].split('|'):
            eq(r['id'] + ' rule ' + rule + ' in scheme', f'| {rule} |' in scheme, True)
        eq(r['id'] + ' basis only with personal data', bool(r['lawful_basis']), r['personal_data'] == 'نعم')
        if r['allowed_kind'] == 'generative':
            eq(r['id'] + ' generative only on Public (G1)', r['data_class'], 'عام')
    total, _ = score_placement(SOL / 'key_placement.csv')
    eq('key scores itself', total, 50)
    total, _ = score_placement(LABS / 'files' / 'placement_template.csv')
    eq('empty template scores', total, 0)


# ---------------------------------------------------------------- stand-in runs (labs 1.2 and 1.3)
def check_runs(tmp):
    env = {'LDS208_RUNS': tmp}
    r = run(['harness/compare.py', '--standin'], env)
    if r.returncode:
        diff('compare.py --standin failed: ' + r.stderr[-400:])
        return
    folder = next(Path(tmp).glob('compare-*'))
    sheet = {row['side']: row for row in read_csv(folder / 'summary.csv')}
    for side, want in KEY['lab_1_2'].items():
        for k, v in want.items():
            eq(f'1.2 {side} {k}', sheet[side][k], v)
    v = run(['harness/verify_log.py', str(folder / 'log.jsonl')])
    eq('1.2 verify_log exit', v.returncode, 0)
    for sc, want in KEY['lab_1_3'].items():
        r = run(['harness/drill.py', '--scenario', sc, '--standin'], env)
        if r.returncode:
            diff(f'drill {sc} failed: ' + r.stderr[-400:])
            continue
        head = json.loads((next(Path(tmp).glob(f'drill-{sc.lower()}-*')) / 'drill_log.jsonl').read_text(encoding='utf-8').splitlines()[0])
        for k, v in want.items():
            eq(f'1.3 {sc} {k}', head[k], v)
        v = run(['harness/verify_log.py', str(next(Path(tmp).glob(f'drill-{sc.lower()}-*')) / 'drill_log.jsonl')])
        eq(f'1.3 {sc} verify_log exit', v.returncode, 0)
    for p in Path(tmp).rglob('*.json*'):
        text = p.read_text(encoding='utf-8')
        if str(LABS) in text or ':\\\\' in text or text.count(':/') > text.count('://'):
            diff(f'{p.name} records a path on this computer')


def check_start_state(tmp):
    env = {'LDS208_RUNS': tmp}
    cases = [(['harness/compare.py'], 'no configuration'), (['harness/drill.py', '--scenario', 'S2'], 'no configuration'),
             (['harness/drill.py', '--scenario', 'S7'], 'S1, S2, S3 or S4'), (['harness/drill.py'], 'name a scenario'),
             (['harness/verify_log.py'], 'name a log'), (['harness/verify_log.py', 'nothing.jsonl'], 'no log')]
    if (LABS / 'harness' / 'config.json').exists():
        cases = cases[2:]
    for args, words in cases:
        r = run(args, env)
        out = r.stdout + r.stderr
        if 'Traceback' in out or words not in out:
            diff(f'{" ".join(args)}: expected a message with "{words}", got: {out[-300:]}')


def main():
    if '--placement' in sys.argv:
        path = Path(sys.argv[sys.argv.index('--placement') + 1])
        total, lines = score_placement(path)
        print('\n'.join(lines))
        print(f'total: {total}/50')
        return
    g = run(['_tools/make_files.py', '--check'])
    print('generator: ' + g.stdout.strip())
    if g.returncode:
        diff('generator output differs from the files on disk')
    check_placement_key()
    with tempfile.TemporaryDirectory() as tmp:
        check_runs(tmp)
        check_start_state(tmp)
    print('PASS' if not fails else f'{fails} difference(s)')
    sys.exit(1 if fails else 0)


if __name__ == '__main__':
    main()
