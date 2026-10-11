import json
import tempfile
import io
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from geo_search_lab.logging.search_capture import capture, observe_payload


class SearchCaptureTests(unittest.TestCase):
    def test_retains_order_duplicates_and_engine_metadata(self):
        payload = {'results': [{'url': 'https://b.example', 'engines': ['bing'], 'positions': [7]},
                               {'url': 'https://a.example', 'engines': ['google']},
                               {'url': 'https://b.example', 'unknown': '中英'}],
                   'unresponsive_engines': [['duckduckgo', 'CAPTCHA']]}
        result = observe_payload(payload)
        self.assertEqual([x['result'] for x in result['ranked_results']], payload['results'])
        self.assertEqual([x['aggregate_rank'] for x in result['ranked_results']], [1, 2, 3])
        self.assertEqual(result['unresponsive_engines'], payload['unresponsive_engines'])

    def test_malformed_response_is_not_a_success(self):
        for payload in (None, [], {}, {'results': 'bad'}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                observe_payload(payload)

    def test_transport_failure_has_durable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('urllib.request.urlopen', side_effect=URLError('fixture offline')):
                result = capture('http://127.0.0.1:8888', {'q': '中文 & test', 'format': 'json'}, Path(directory), 'test')
            self.assertEqual(result['status'], 'transport_error')
            persisted = json.loads((Path(directory) / 'test.json').read_text(encoding='utf-8'))
            self.assertIn('fixture offline', persisted['error'])
            self.assertTrue((Path(directory) / 'test.response.bin').exists())

    def test_raw_response_is_preserved_and_partial_failure_is_visible(self):
        raw = b'{"results":[{"url":"https://example.org","positions":[2]}], "unresponsive_engines":[["bing","CAPTCHA"]]}'
        response = io.BytesIO(raw)
        response.status = 200
        response.headers = {'Content-Type': 'application/json', 'Server-Timing': 'total;dur=12'}
        with tempfile.TemporaryDirectory() as directory:
            with patch('urllib.request.urlopen', return_value=response):
                result = capture('http://127.0.0.1:8888', {'q': 'test'}, Path(directory), 'partial')
            self.assertEqual(result['status'], 'partial')
            self.assertEqual(result['response_headers']['Server-Timing'], 'total;dur=12')
            self.assertEqual((Path(directory) / 'partial.response.bin').read_bytes(), raw)

    def test_html_response_does_not_masquerade_as_empty_search(self):
        response = io.BytesIO(b'<html>JSON API disabled</html>')
        response.status = 200
        response.headers = {'Content-Type': 'text/html'}
        with tempfile.TemporaryDirectory() as directory:
            with patch('urllib.request.urlopen', return_value=response):
                result = capture('http://127.0.0.1:8888', {'q': 'test'}, Path(directory), 'html')
            self.assertEqual(result['status'], 'invalid_payload')


if __name__ == '__main__':
    unittest.main()
