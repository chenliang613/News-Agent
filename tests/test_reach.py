import json
import subprocess
import unittest
from unittest.mock import patch
from news_agent import reach


class ReachTests(unittest.TestCase):
    def test_disabled_does_not_execute(self):
        with patch.object(reach.subprocess, 'run') as run:
            self.assertEqual(reach.fetch(['AI'], {}), [])
            run.assert_not_called()

    @patch.object(reach.shutil, 'which', return_value='/bin/agent-reach')
    def test_maps_deduplicates_and_bounds_queries(self, _):
        item = {'title': 'AI standard', 'url': 'https://example.org/news',
                'text': 'Evidence', 'published_at': '2026-11-01T10:00:00Z'}
        response = subprocess.CompletedProcess([], 0, json.dumps({'ok': True, 'items': [item, item, {'url': 'file:///secret'}]}))
        with patch.object(reach.subprocess, 'run', return_value=response) as run:
            articles = reach.fetch(['AI', 'AI', 'data'], {'enabled': True, 'max_queries': 1})
            self.assertEqual(len(articles), 1)
            self.assertIsNotNone(articles[0].published_at)
            self.assertEqual(articles[0].content, '')
            self.assertEqual(run.call_count, 1)

    @patch.object(reach.shutil, 'which', return_value='/bin/agent-reach')
    def test_block_stops_remaining_queries(self, _):
        response = subprocess.CompletedProcess([], 0, json.dumps({'ok': False, 'error': {'code': 'RATE_LIMITED'}}))
        with patch.object(reach.subprocess, 'run', return_value=response) as run:
            self.assertEqual(reach.fetch(['AI', 'data'], {'enabled': True}), [])
            self.assertEqual(run.call_count, 1)

    @patch.object(reach.shutil, 'which', return_value='/bin/agent-reach')
    def test_timeout_preserves_pipeline(self, _):
        with patch.object(reach.subprocess, 'run', side_effect=subprocess.TimeoutExpired('reach', 20)):
            self.assertEqual(reach.fetch(['AI'], {'enabled': True}), [])

    @patch.object(reach.shutil, 'which', return_value='/bin/agent-reach')
    def test_malformed_payloads_do_not_crash_collection(self, _):
        for payload in ({'ok': False, 'error': 'broken'}, {'ok': True, 'items': 42}):
            with self.subTest(payload=payload), patch.object(reach.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(payload))):
                self.assertEqual([], reach.fetch(['AI'], {'enabled': True}))

    @patch.object(reach.shutil, 'which', return_value='/bin/agent-reach')
    def test_nonzero_exit_is_not_accepted(self, _):
        payload = {'ok': True, 'items': [{'title': 'AI', 'url': 'https://example.com'}]}
        with patch.object(reach.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, json.dumps(payload))):
            self.assertEqual([], reach.fetch(['AI'], {'enabled': True}))
