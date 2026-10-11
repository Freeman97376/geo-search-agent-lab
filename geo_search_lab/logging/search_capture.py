"""Lossless search-response capture; never reorder or rewrite search results."""
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def observe_payload(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('results'), list):
        raise ValueError('Expected SearXNG JSON object with a results list')
    # Rank is SearXNG aggregate array order, not a fabricated engine rank.
    ranked = [{'aggregate_rank': i, 'result': result}
              for i, result in enumerate(payload['results'], 1)]
    return {'ranked_results': ranked,
            'unresponsive_engines': payload.get('unresponsive_engines', []),
            'result_count': len(ranked)}


def capture(base_url, params, directory, request_id, timeout=25):
    directory.mkdir(parents=True, exist_ok=True)
    url = base_url.rstrip('/') + '/search?' + urllib.parse.urlencode(params)
    started = time.perf_counter()
    raw = b''
    record = {'schema_version': 1, 'request_id': request_id,
              'captured_at_utc': datetime.now(timezone.utc).isoformat(),
              'url': url, 'params': params, 'status': 'transport_error'}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept': 'application/json'}), timeout=timeout) as response:
            raw = response.read()
            record['http_status'] = response.status
            record['response_headers'] = dict(response.headers)
        record.update(observe_payload(json.loads(raw)))
        record['status'] = 'ok' if record['result_count'] else 'empty'
        if record['unresponsive_engines']:
            record['status'] = 'partial' if record['result_count'] else 'engine_failure'
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        record.update(status='http_error', http_status=exc.code, error=str(exc))
    except (OSError, ValueError, urllib.error.URLError) as exc:
        record['error'] = f'{type(exc).__name__}: {exc}'
        if raw:
            record['status'] = 'invalid_payload'
    finally:
        record['elapsed_ms'] = round((time.perf_counter() - started) * 1000, 3)
        record['response_sha256'] = hashlib.sha256(raw).hexdigest()
        record['raw_response_file'] = f'{request_id}.response.bin'
        (directory / record['raw_response_file']).write_bytes(raw)
        (directory / f'{request_id}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return record
