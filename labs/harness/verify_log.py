# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Checks a run log of labs 1.2 or 1.3 of LDS-EXE-208, for the participant and the assessor.

It scores every logged answer again with the course's rules, compares each verdict with the one
in the log, recomputes the numbers of the results sheet from the log alone, and prints a
fingerprint of the log. The assessor compares the fingerprint and the numbers with the files the
participant handed in.

    uv run labs/harness/verify_log.py labs/harness/runs/<run id>/log.jsonl
    uv run labs/harness/verify_log.py labs/harness/runs/<run id>/drill_log.jsonl
"""
import hashlib
import sys
from pathlib import Path

import lib


def main():
    if len(sys.argv) < 2:
        sys.exit('name a log: uv run labs/harness/verify_log.py labs/harness/runs/<run id>/log.jsonl')
    path = Path(sys.argv[1])
    if not path.exists():
        sys.exit(f'no log at {lib.rel(path)}: run compare.py or drill.py first')
    try:
        recs = lib.read_jsonl(path)
    except ValueError:
        sys.exit(f'{lib.rel(path)} is not a run log (a line is not JSON)')
    head = [r for r in recs if r.get('type') in ('run', 'drill')]
    rows = [r for r in recs if r.get('type') == 'item']
    if not head or not rows:
        sys.exit(f'{lib.rel(path)} holds no run header or no items')
    items = {it['id']: it for it in lib.items()}
    mismatch = []
    for r in rows:
        it = items.get(r['item'])
        if it is None:
            mismatch.append(f'{r["side"]} {r["item"]}: not an item of files/items.csv')
            continue
        again = False if r.get('error') else lib.score(it, r.get('answer'))
        if again != r['pass']:
            mismatch.append(f'{r["side"]} {r["item"]}: log says {r["pass"]}, the rules say {again}')
    h = head[0]
    print(f'log: {lib.rel(path)}; run {h["run"]}; mode {h["mode"]}'
          + ('  (scripted stand-in: not model results)' if h['mode'] == 'standin' else ''))
    if h.get('items_sha') and h['items_sha'] != lib.file_sha(lib.FILES / 'items.csv')[:16]:
        mismatch.append('the items file differs from the one the run used')
    for side in ('hosted', 'local'):
        rs = [r for r in rows if r['side'] == side]
        if not rs:
            continue
        for r in rs:
            r['pass'] = False if r.get('error') else lib.score(items[r['item']], r.get('answer')) if r['item'] in items else False
        sm = lib.summarise(rs, {}, None)
        print(f'{side} ({rs[0]["model"]}): Arabic {sm["pass_ar"]}/{sm["n_ar"]}, English {sm["pass_en"]}/{sm["n_en"]}, '
              f'median {lib.fmt(sm["median_ms"], " ms", none="no answers")}, '
              f'90th percentile {lib.fmt(sm["p90_ms"], " ms", none="no answers")}, ' +
              (f'cost per 1,000 items {lib.fmt(sm["cost_usd_per_1000"], " USD")}' if side == 'hosted' else 'no per-token price') + f', errors {sm["errors"]}')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f'fingerprint: {digest[:16]}')
    if mismatch:
        print('DIFFERENCES:')
        print('\n'.join('  ' + m for m in mismatch))
        sys.exit(1)
    print('every verdict in the log agrees with the rules')


if __name__ == '__main__':
    main()
