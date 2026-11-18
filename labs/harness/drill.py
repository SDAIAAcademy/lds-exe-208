# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Lab 1.3 of LDS-EXE-208: the withdrawal drill.

The main provider is the "hosted" side of harness/config.json and the fallback is the "local"
side. The drill runs the 20 items on the main provider, then injects the scenario into the
router (the provider itself is not touched: the failure is simulated here), switches to the
fallback, runs the same items there and logs what breaks.

    uv run labs/harness/drill.py --scenario S2             # with the models in config.json
    uv run labs/harness/drill.py --scenario S2 --standin   # the scripted stand-in: no model is called

Scenarios (files/scenarios.md): S1 export restriction, S2 model retirement, S3 price change,
S4 outage from an energy and compute limit. It writes harness/runs/<run id>/drill_log.jsonl
and drill_report.md. The stand-in's answers and latencies are scripted by the course, not
produced by a model, and every log line says so.
"""
import sys

import lib
from compare import DEFAULT_CONFIG, run_side

INJECT = {
    'S1': ('HTTP 451 (simulated: the provider withdrew the service from this region)', True),
    'S2': ('HTTP 404 (simulated: model retired)', True),
    'S3': ('none: the service answers; the price changes', False),
    'S4': ('timeout (simulated: the in-country region is down, then back at half capacity)', True),
}
WORKING_DAYS = 22


def monthly_cost_usd(price_row, vol):
    return float(vol['per_month']) * (float(vol['avg_input_tokens']) * float(price_row['input_usd_per_million_tokens'])
                                      + float(vol['avg_output_tokens']) * float(price_row['output_usd_per_million_tokens'])) / 1e6


def main():
    args = sys.argv[1:]
    if '--scenario' not in args or args.index('--scenario') + 1 >= len(args):
        sys.exit('name a scenario: --scenario S1, S2, S3 or S4')
    sc = args[args.index('--scenario') + 1].upper()
    if sc not in INJECT:
        sys.exit('the scenario is S1, S2, S3 or S4')
    mode = 'standin' if '--standin' in args else 'model'
    cfg = lib.standin_config() if mode == 'standin' else lib.load_config(DEFAULT_CONFIG)
    items = lib.items()
    standin = lib.standin_answers() if mode == 'standin' else None
    run_id = lib.new_run_id(mode, 'drill-' + sc.lower())
    if mode == 'standin':
        print('STAND-IN RUN: scripted answers and latencies written by the course; no model is called.')

    print(f'1. before: the main provider ({cfg["hosted"]["model"]}) on the 20 items')
    before = run_side('hosted', cfg, mode, run_id, items, standin)
    injected, fails = INJECT[sc]
    print(f'2. scenario {sc}: injected into the router: {injected}')
    signal = 'first failed call' if fails else 'price sheet dated ' + [p for p in lib.read_csv(lib.FILES / 'price_sheet.csv') if p['note'].startswith('announced')][0]['valid_from']
    print(f'   the router sees it at: {signal}; it switches to the fallback ({cfg["local"]["model"]})')
    print('3. after: the fallback on the same 20 items')
    after = run_side('local', cfg, mode, run_id, items, standin)

    sb, sa = lib.summarise(before, cfg['hosted'], items), lib.summarise(after, cfg['local'], items)
    by_item = {r['item']: r for r in before}
    broke = [r['item'] for r in after if by_item[r['item']]['pass'] and not r['pass']]
    mended = [r['item'] for r in after if not by_item[r['item']]['pass'] and r['pass']]

    # capacity of the fallback at the busiest hour of the generative workloads
    vols = {v['workload']: v for v in lib.read_csv(lib.FILES / 'volumes.csv')}
    slots = lib.money(cfg['local'].get('parallel_slots')) or 1
    cap = None if not sa['median_ms'] else round(3600 / (sa['median_ms'] / 1000) * slots)
    demand = {w: round(float(vols[w]['per_month']) / WORKING_DAYS * float(vols[w]['busiest_hour_share_of_day']), 1)
              for w in ('W01', 'W02')}

    # S3: monthly cost of W01 and W02 on the main provider, before and after the announced price
    prices = lib.read_csv(lib.FILES / 'price_sheet.csv')
    inc = [p for p in prices if p['model'] == 'orv-large-3' and p['registered_with_cst'] == 'yes']
    now_row, new_row = inc[0], inc[-1]
    cost_now = sum(monthly_cost_usd(now_row, vols[w]) for w in ('W01', 'W02')) * lib.SAR_PER_USD
    cost_new = sum(monthly_cost_usd(new_row, vols[w]) for w in ('W01', 'W02')) * lib.SAR_PER_USD

    lines = [
        f'# Drill {sc}: {run_id}',
        '',
        f'Mode: {mode}' + (' (scripted stand-in: not model results)' if mode == 'standin' else ''),
        f'Injected: {injected}',
        f'Signal the router saw: {signal}',
        '',
        '| | main provider (before) | fallback (after) |',
        '|---|---|---|',
        f'| model | {cfg["hosted"]["model"]} | {cfg["local"]["model"]} |',
        f'| Arabic items passed | {sb["pass_ar"]}/{sb["n_ar"]} | {sa["pass_ar"]}/{sa["n_ar"]} |',
        f'| English items passed | {sb["pass_en"]}/{sb["n_en"]} | {sa["pass_en"]}/{sa["n_en"]} |',
        f'| median latency, ms | {lib.fmt(sb["median_ms"], none="no answers")} | {lib.fmt(sa["median_ms"], none="no answers")} |',
        f'| 90th percentile, ms | {lib.fmt(sb["p90_ms"], none="no answers")} | {lib.fmt(sa["p90_ms"], none="no answers")} |',
        f'| errors | {sb["errors"]} | {sa["errors"]} |',
        '',
        f'Broke on the fallback (passed before, fail after): {" ".join(broke) or "none"}',
        f'Mended on the fallback (failed before, pass after): {" ".join(mended) or "none"}',
        '',
        f'Fallback capacity at its median latency and {int(slots)} parallel slot(s): {lib.fmt(cap, none="no answers")} requests an hour.',
        f'Busiest-hour demand (volumes.csv, {WORKING_DAYS} working days): W01 {demand["W01"]}, W02 {demand["W02"]} requests an hour.',
        '',
        f'W01 and W02 on the main provider in the Kingdom: {round(cost_now, 2)} riyals a month now, {round(cost_new, 2)} after the announced price.',
        '',
        'Write in the register what the drill could not see: who noticed, who decided, how long the switch took by hand, and what users saw.',
    ]
    out = lib.RUNS / run_id
    header = {'type': 'drill', 'run': run_id, 'mode': mode, 'date': lib.today(mode), 'harness': lib.VERSION,
              'scenario': sc, 'injected': injected, 'signal': signal,
              'main': {'model': cfg['hosted']['model'], 'data_path': lib.data_path(cfg['hosted'])},
              'fallback': {'model': cfg['local']['model'], 'data_path': lib.data_path(cfg['local'])},
              'broke': broke, 'mended': mended, 'fallback_capacity_per_hour': cap, 'busiest_hour_demand': demand,
              'w01_w02_sar_per_month_now': round(cost_now, 2), 'w01_w02_sar_per_month_new': round(cost_new, 2)}
    lib.write_jsonl(out / 'drill_log.jsonl', [header] + before + after)
    (out / 'drill_report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines[6:-2]))
    print(f'saved {lib.rel(out)}/drill_log.jsonl and drill_report.md')


if __name__ == '__main__':
    main()
