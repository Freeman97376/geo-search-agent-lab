"""Explicit engine probes; no auto-disable, provider switch, or baseline promotion."""
import argparse
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from geo_search_lab.logging.search_capture import capture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 5:
        parser.error('--rounds must be between 1 and 5')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = ROOT / 'data/live_runs' / ('engine-probe-' + stamp)
    output.mkdir(parents=True)
    try:
        with urllib.request.urlopen('http://127.0.0.1:8888/config', timeout=5) as response:
            raw = response.read()
            json.loads(raw)
            (output / 'runtime-config.json').write_bytes(raw)
    except Exception as exc:
        summary = {'status': 'blocked', 'reason': f'{type(exc).__name__}: {exc}',
                   'engines_tested': [], 'baseline_engines': None,
                   'captured_at_utc': datetime.now(timezone.utc).isoformat()}
        (ROOT / 'docs/searxng_probe_latest.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(summary)); return 2
    queries = [('en', 'SearXNG search engine documentation'), ('zh-CN', '开源 搜索引擎 SearXNG')]
    records = []
    for round_id in range(1, args.rounds + 1):
        for engine in ('google', 'bing', 'duckduckgo', 'all'):
            for language, query in queries:
                params = {'q': query, 'format': 'json', 'language': language}
                if engine != 'all':
                    params['engines'] = engine
                request_id = f'r{round_id}-{engine}-{language}'
                result = capture('http://127.0.0.1:8888', params, output, request_id)
                records.append({k: result.get(k) for k in ('request_id', 'status', 'result_count', 'elapsed_ms', 'unresponsive_engines', 'error')})
                print(json.dumps(records[-1], ensure_ascii=False), flush=True)
    summary = {'status': 'probed', 'candidate_engines': ['google', 'bing', 'duckduckgo'],
               'baseline_engines': None, 'note': 'Candidate engine probes do not freeze a baseline.',
               'evidence_directory': output.relative_to(ROOT).as_posix(), 'requests': records}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (ROOT / 'docs/searxng_probe_latest.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0 if all(r['status'] == 'ok' for r in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())
