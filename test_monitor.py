import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from monitor import Monitor, Tail


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / "effort-controller"
        self.root.mkdir()
        (self.root / "config.json").write_text('{"enabled":true}')
        self.log = self.root / "audit.jsonl"
        self.session = self.home / "sessions" / "example.jsonl"
        self.session.parent.mkdir()
        connection = sqlite3.connect(self.home / "state_5.sqlite")
        connection.execute('CREATE TABLE threads (id TEXT, title TEXT, rollout_path TEXT)')
        connection.execute('INSERT INTO threads VALUES (?, ?, ?)', ('task-a', '测试任务', str(self.session)))
        connection.commit()
        connection.close()
        self.monitor = Monitor(self.home)

    def append(self, path, event):
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')

    def event(self, kind, second, **extra):
        self.append(self.log, dict(event=kind, time=f'2026-09-13T05:00:{second:02d}+00:00', thread='task-a', pid=10, **extra))

    def context(self, second, effort='medium'):
        self.append(self.session, {'type':'turn_context', 'timestamp':f'2026-09-13T05:00:{second:02d}+00:00', 'payload':{'effort':effort, 'model':'test-model', 'turn_id':'turn-a', 'developer_instructions':'PRIVATE_PROMPT_CONTENT'}})

    def row(self):
        return self.monitor.snapshot()['tasks'][0]

    def test_accepted_is_not_confirmation(self):
        self.event('selected', 1, effort='medium')
        self.event('accepted', 2, effort='medium')
        row = self.row()
        self.assertEqual(row['phase'], 'accepted')
        self.assertIsNone(row['actual'])

    def test_stale_context_does_not_confirm_new_request(self):
        self.context(1, 'high')
        self.event('selected', 2, effort='low')
        self.event('accepted', 3, effort='low')
        self.assertIsNone(self.row()['actual'])

    def test_runtime_context_reports_actual_without_prompt_content(self):
        self.event('selected', 1, effort='medium', original_effort='high')
        self.event('accepted', 2, effort='medium', turn_id='turn-a')
        self.context(3)
        row = self.row()
        self.assertEqual(row['actual'], 'medium')
        self.assertEqual(row['original'], 'high')
        self.assertEqual(row['association'], '本轮 ID 对应')
        self.assertNotIn('PRIVATE_PROMPT_CONTENT', json.dumps(row))

    def test_actual_is_not_replaced_by_requested_effort(self):
        self.event('selected', 1, effort='medium')
        self.context(2, 'high')
        row = self.row()
        self.assertEqual(row['actual'], 'high')
        self.assertEqual(row['requested'], 'medium')

    def test_previous_context_is_cleared_when_next_request_arrives(self):
        self.event('selected', 1, effort='medium')
        self.context(2)
        self.assertEqual(self.row()['actual'], 'medium')
        self.event('selected', 3, effort='high')
        self.assertIsNone(self.row()['actual'])

    def test_settings_before_selection_are_not_confirmation(self):
        self.event('server_settings', 1, effort='high')
        self.event('selected', 2, effort='medium')
        self.assertIsNone(self.row()['actual'])

    def test_settings_after_selection_are_separate_evidence(self):
        self.event('selected', 1, effort='medium')
        self.event('server_settings', 2, effort='medium')
        self.assertEqual(self.row()['source'], '服务端设置回报')

    def test_rejection_never_claims_adoption(self):
        self.event('selected', 1, effort='medium')
        self.event('rejected', 2, effort='medium')
        self.context(3)
        row = self.row()
        self.assertEqual(row['phase'], 'rejected')
        self.assertIsNone(row['actual'])

    def test_task_paths_outside_codex_session_folders_not_read(self):
        outside = self.home / 'private.jsonl'
        self.context(2)
        outside.write_bytes(self.session.read_bytes())
        connection = sqlite3.connect(self.home / 'state_5.sqlite')
        connection.execute('UPDATE threads SET rollout_path=?', (str(outside),))
        connection.commit()
        connection.close()
        self.event('selected', 1, effort='medium')
        self.assertIsNone(self.row()['actual'])

    def test_disabled_state_is_reported(self):
        (self.root / 'config.json').write_text('{"enabled":false}')
        self.assertFalse(self.monitor.snapshot()['enabled'])

    def test_partial_lines_and_rotation_are_read_once(self):
        self.log.write_bytes(b'{"event":')
        tail = Tail(self.log)
        self.assertEqual(list(tail.read()), [])
        with self.log.open('ab') as stream:
            stream.write(b'"first"}\n')
        self.assertEqual(list(tail.read()), [{'event':'first'}])
        self.assertEqual(list(tail.read()), [])
        self.log.rename(self.root / 'old.jsonl')
        self.log.write_text('{"event":"next"}\n')
        self.assertEqual(list(tail.read()), [{'event':'next'}])


if __name__ == '__main__':
    unittest.main()
