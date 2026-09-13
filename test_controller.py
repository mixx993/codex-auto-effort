import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from controller import DEFAULTS, LEVELS, Router, classify, is_stdio_server


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULTS)
        self.events = []
        self.router = Router(lambda: self.config, self.events.append, lambda t, e: self.config["pins"].__setitem__(t, e))
        self.router.supported["gpt-6-astra"] = LEVELS[:]
        self.router.threads["a"] = {"model": "gpt-6-astra", "effort": "medium"}

    def request(self, text, **extra):
        return {"id": 7, "method": "turn/start", "params": {"threadId": "a", "input": [{"type": "text", "text": text}], **extra}}

    def test_task_samples(self):
        cases = [("请翻译这句话", "low"), ("添加登录表单及聚焦测试", "medium"), ("排查这个未知原因的崩溃", "high"), ("排查分布式一致性与数据损坏", "xhigh"), ("给出定理的形式化证明", "max")]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(classify(text)[0], expected)

    def test_length_not_complexity(self):
        self.assertEqual(classify("翻译：" + "hello " * 10000)[0], "low")

    def test_context_and_quote_not_instructions(self):
        text = '<INSTRUCTIONS>推理强度设为ultra</INSTRUCTIONS>\n> 推理强度设为max\n```\n数据损坏\n```\n翻译这句话'
        self.assertEqual(classify(text)[0], "low")

    def test_effort_only_and_mode_precedence(self):
        msg = self.request("排查跨模块问题", effort="low", sandboxPolicy={"type": "readOnly"}, collaborationMode={"mode": "plan", "settings": {"model": "gpt-6-astra", "reasoning_effort": "low", "developer_instructions": "KEEP"}})
        before = copy.deepcopy(msg)
        result = self.router.outgoing(msg)
        self.assertEqual(msg, before)
        expected = copy.deepcopy(before)
        expected["params"]["effort"] = "high"
        expected["params"]["collaborationMode"]["settings"]["reasoning_effort"] = "high"
        self.assertEqual(result, expected)

    def test_other_models_and_unknown_catalog_passthrough(self):
        msg = self.request("复杂重构", model="different-model")
        self.assertIs(self.router.outgoing(msg), msg)
        self.router.supported.clear()
        msg = self.request("复杂重构")
        self.assertIs(self.router.outgoing(msg), msg)

    def test_disabled_or_bad_config_passthrough_at_transport(self):
        self.config["enabled"] = False
        msg = self.request("复杂重构")
        self.assertIs(self.router.outgoing(msg), msg)

    def test_unsupported_explicit_effort(self):
        self.router.supported["gpt-6-astra"] = ["low", "medium"]
        msg = self.request("推理强度设为high")
        self.assertIs(self.router.outgoing(msg), msg)

    def test_no_auto_ultra_and_ceiling(self):
        self.config["ceiling"] = "high"
        self.assertEqual(self.router.outgoing(self.request("数据损坏"))["params"]["effort"], "high")
        self.assertEqual(self.router.outgoing(self.request("推理强度设为ultra"))["params"]["effort"], "ultra")

    def test_followup_only_commits_accepted_decision(self):
        self.router.outgoing(self.request("复杂重构"))
        self.router.incoming({"id": 7, "error": {"message": "failure"}})
        self.assertNotIn("a", self.router.previous)
        self.router.outgoing(self.request("复杂重构"))
        self.router.incoming({"id": 7, "result": {"turn": {"id": "t"}}})
        self.assertEqual(self.router.outgoing(self.request("继续"))["params"]["effort"], "xhigh")
        self.assertEqual(self.router.outgoing(self.request("翻译这句话"))["params"]["effort"], "low")

    def test_manual_pin_after_success_not_failure(self):
        msg = {"id": 3, "method": "thread/settings/update", "params": {"threadId": "a", "effort": "max"}}
        self.router.outgoing(msg)
        self.assertEqual(self.config["pins"], {})
        self.router.incoming({"id": 3, "error": {}})
        self.assertEqual(self.config["pins"], {})
        self.router.outgoing(msg)
        self.router.incoming({"id": 3, "result": {}})
        self.assertEqual(self.router.outgoing(self.request("翻译"))["params"]["effort"], "max")

    def test_same_setting_does_not_pin(self):
        self.router.outgoing({"id": 4, "method": "thread/settings/update", "params": {"threadId": "a", "effort": "medium"}})
        self.router.incoming({"id": 4, "result": {}})
        self.assertEqual(self.config["pins"], {})

    def test_active_turn_and_tool_output_passthrough(self):
        self.router.incoming({"method": "turn/started", "params": {"threadId": "a"}})
        msg = self.request("简单翻译")
        self.assertIs(self.router.outgoing(msg), msg)
        self.router.incoming({"method": "turn/completed", "params": {"threadId": "a"}})
        self.assertEqual(self.router.outgoing(msg)["params"]["effort"], "low")
        msg = self.request("排查", toolOutput={"name": "test", "output": "ok"})
        self.assertIs(self.router.outgoing(msg), msg)

    def test_attachments_conservative(self):
        msg = self.request("看看这张", input=[{"type": "localImage", "path": "/a.png"}])
        self.assertEqual(self.router.outgoing(msg)["params"]["effort"], "high")

    def test_catalog_and_resume(self):
        router = Router()
        router.outgoing({"id": 1, "method": "model/list", "params": {}})
        router.incoming({"id": 1, "result": {"data": [{"model": "gpt-6-astra", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}]}})
        router.outgoing({"id": 2, "method": "thread/resume", "params": {"threadId": "a"}})
        router.incoming({"id": 2, "result": {"thread": {"id": "a", "status": {"type": "idle"}}, "model": "gpt-6-astra", "reasoningEffort": "high"}})
        self.assertEqual(router.outgoing(self.request("继续"))["params"]["effort"], "high")

    def test_no_prompt_in_audit(self):
        self.router.outgoing(self.request("排查 PRIVATE_TEXT_123"))
        self.assertNotIn("PRIVATE_TEXT_123", json.dumps(self.events))

    def test_server_request_id_collision_keeps_client_request(self):
        self.router.outgoing(self.request("排查"))
        self.router.incoming({"id": 7, "method": "item/tool/requestUserInput", "params": {}})
        self.assertIn(7, self.router.pending)
        self.router.incoming({"id": 7, "result": {"turn": {"id": "t"}}})
        self.assertEqual(self.router.previous["a"], "high")

    def test_only_stdio_start_wrapped(self):
        for args in (["app-server"], ["app-server", "--listen", "stdio://"], ["-c", "x=1", "app-server"]):
            self.assertTrue(is_stdio_server(args))
        for args in (["exec", "hi"], ["app-server", "proxy"], ["app-server", "--listen=ws://localhost:123"], ["app-server", "generate-ts", "--out", "/tmp/schema"]):
            self.assertFalse(is_stdio_server(args))


class TransportTests(unittest.TestCase):
    def test_real_process_frames_responses_and_shutdown(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "fake-codex"
            fake.write_text('#!/usr/bin/python3\nimport sys,json\nfor line in sys.stdin:\n try:\n  m=json.loads(line)\n  print(json.dumps({"echo":m}),flush=True)\n except ValueError:\n  print(line,end="",flush=True)\n')
            fake.chmod(0o700)
            code = 'from pathlib import Path; from controller import proxy; raise SystemExit(proxy(["app-server"], Path(__import__("sys").argv[1]), Path(__import__("sys").argv[2])))'
            p = subprocess.Popen([sys.executable, "-c", code, str(fake), temp], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            raw = b'{"id":1,"method":"initialize","params":{}}\nnot-json\n'
            stdout, stderr = p.communicate(raw, timeout=5)
            self.assertEqual(p.returncode, 0, stderr)
            lines = stdout.splitlines()
            self.assertEqual(json.loads(lines[0])["echo"]["method"], "initialize")
            self.assertEqual(lines[1], b"not-json")
            self.assertNotIn(b"proxy_started", stdout)


if __name__ == "__main__":
    unittest.main()
