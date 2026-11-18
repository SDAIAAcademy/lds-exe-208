# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Lab 1.2 of LDS-EXE-208: one bilingual task set on a hosted model and a local model.

Sends the 20 items of files/items.csv (classed Public, synthetic) to the two models named in
harness/config.json, scores every answer with the course's rules, and writes a run folder:

    harness/runs/<run id>/log.jsonl     every request: answer, verdict, latency, tokens, cost
    harness/runs/<run id>/results.csv   one row per item and model
    harness/runs/<run id>/summary.csv   the results sheet: quality by language, latency, cost, data path

    uv run labs/harness/compare.py                 # both models from config.json
    uv run labs/harness/compare.py --standin       # the scripted stand-in: no model is called
    uv run labs/harness/compare.py --only local    # one side

The stand-in was written by the course to show how the sheet is read. Its answers and latencies
are scripted, not produced by a model, and every line of its log says so ("mode": "standin").
Only the synthetic items of the course are sent. Never put your organisation's data in.
"""
import csv
import sys

import lib

DEFAULT_CONFIG = lib.HERE / 'config.json'


def run_side(side, cfg, mode, run_id, items, standin):
    sc = cfg[side]
    rows = []
    fails_in_a_row = 0
    for it in items:
        rec = {'type': 'item', 'run': run_id, 'mode': mode, 'side': side, 'model': sc['model'],
               'item': it['id'], 'lang': it['lang'], 'check': it['check'],
               'prompt_sha': lib.hashlib.sha256(it['prompt'].encode('utf-8')).hexdigest()[:12],
               'answer': None, 'pass': False, 'latency_ms': None, 'input_tokens': None,
               'output_tokens': None, 'tokens_source': None, 'cost_usd': None, 'error': None}
        if mode == 'standin':
            ans, ms = standin[(side, it['id'])]
            tin, tout, src = lib.est_tokens(lib.SYSTEM[it['lang']] + it['prompt']), lib.est_tokens(ans), 'estimated'
        else:
            try:
                ans, ms, tin, tout, src = lib.call_model(sc, it['lang'], it['prompt'])
                fails_in_a_row = 0
            except lib.CallFailed as e:
                rec['error'] = str(e)
                fails_in_a_row += 1
                print(f'  {side} {it["id"]}: {e}')
                rows.append(rec)
                if fails_in_a_row == 3:
                    print(f'  {side}: three failures in a row; the rest of this side is skipped. Check the runner, the address and the key.')
                    for rest in items[len(rows):]:
                        rows.append(dict(rec, item=rest['id'], lang=rest['lang'], check=rest['check'],
                                         prompt_sha=lib.hashlib.sha256(rest['prompt'].encode('utf-8')).hexdigest()[:12],
                                         error='skipped after three failures'))
                    break
                continue
        rec.update(answer=ans, latency_ms=ms, input_tokens=tin, output_tokens=tout, tokens_source=src,
                   **{'pass': lib.score(it, ans)})
        rows.append(rec)
    price_known = side == 'hosted' and lib.item_cost_usd(sc, 0, 0) is not None
    for r in rows:
        if price_known:
            r['cost_usd'] = 0.0 if r['error'] else lib.item_cost_usd(sc, r['input_tokens'], r['output_tokens'])
        elif side == 'local':
            r['cost_usd'] = 0.0  # no per-token price; the fixed monthly cost is reported separately
    return rows


def main():
    args = sys.argv[1:]
    mode = 'standin' if '--standin' in args else 'model'
    only = args[args.index('--only') + 1] if '--only' in args and args.index('--only') + 1 < len(args) else None
    if only and only not in ('hosted', 'local'):
        sys.exit('--only takes hosted or local')
    cfg = lib.standin_config() if mode == 'standin' else lib.load_config(DEFAULT_CONFIG)
    items = lib.items()
    standin = lib.standin_answers() if mode == 'standin' else None
    run_id = lib.new_run_id(mode, 'compare')
    sides = [only] if only else ['hosted', 'local']
    if mode == 'standin':
        print('STAND-IN RUN: scripted answers and latencies written by the course; no model is called.')
    header = {'type': 'run', 'run': run_id, 'mode': mode, 'date': lib.today(mode), 'harness': lib.VERSION,
              'items_sha': lib.file_sha(lib.FILES / 'items.csv')[:16], 'temperature': 0,
              'sides': {s: {'label': cfg[s].get('label', s), 'model': cfg[s]['model'],
                            'data_path': lib.data_path(cfg[s])} for s in sides}}
    all_rows = []
    for s in sides:
        print(f'{s}: {cfg[s].get("label", s)} ({cfg[s]["model"]})')
        all_rows += run_side(s, cfg, mode, run_id, items, standin)
    out = lib.RUNS / run_id
    lib.write_jsonl(out / 'log.jsonl', [header] + all_rows)
    with open(out / 'results.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['side', 'item', 'lang', 'check', 'pass', 'latency_ms', 'input_tokens', 'output_tokens',
                    'tokens_source', 'cost_usd', 'answer', 'error'])
        for r in all_rows:
            w.writerow([r['side'], r['item'], r['lang'], r['check'], int(r['pass']), r['latency_ms'] or '',
                        r['input_tokens'] or '', r['output_tokens'] or '', r['tokens_source'] or '',
                        '' if r['cost_usd'] is None else round(r['cost_usd'], 8), r['answer'] or '', r['error'] or ''])
    sheet = []
    for s in sides:
        rs = [r for r in all_rows if r['side'] == s]
        sm = lib.summarise(rs, cfg[s], items)
        dp = lib.data_path(cfg[s])
        fixed = lib.money(cfg[s].get('server_sar_per_month')) if s == 'local' else None
        sheet.append([s, cfg[s]['model'], mode,
                      f'{sm["pass_ar"]}/{sm["n_ar"]}', f'{sm["pass_en"]}/{sm["n_en"]}', f'{sm["pass_all"]}/{sm["n_all"]}',
                      lib.fmt(sm['median_ms'], none='no answers'), lib.fmt(sm['p90_ms'], none='no answers'),
                      lib.fmt(sm['cost_usd_per_1000']) if s == 'hosted' else 'no per-token price',
                      lib.fmt(fixed) if s == 'local' else 'none',
                      dp['host'], 'yes' if dp['leaves_device'] else 'no', dp['where_it_runs'], dp['data_use_on_this_tier'],
                      sm['failed_items'], sm['errors']])
        print(f'  quality: Arabic {sm["pass_ar"]}/{sm["n_ar"]}, English {sm["pass_en"]}/{sm["n_en"]}; '
              f'median {lib.fmt(sm["median_ms"], " ms", none="no answers")}, 90th percentile {lib.fmt(sm["p90_ms"], " ms", none="no answers")}; '
              f'failed: {sm["failed_items"] or "none"}; errors: {sm["errors"]}')
    with open(out / 'summary.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, lineterminator='\n')
        w.writerow(['side', 'model', 'mode', 'pass_ar', 'pass_en', 'pass_all', 'median_ms', 'p90_ms',
                    'cost_usd_per_1000_items', 'fixed_sar_per_month', 'host', 'leaves_device', 'where_it_runs',
                    'data_use_on_this_tier', 'failed_items', 'errors'])
        w.writerows(sheet)
    print(f'saved {lib.rel(out)}/log.jsonl, results.csv and summary.csv')


if __name__ == '__main__':
    main()
