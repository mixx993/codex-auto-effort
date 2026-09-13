import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from controller import DEFAULTS, Router, classify, is_stdio_server, read_config


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(DEFAULTS)
        self.router = Router(lambda: self.config, pin=lambda t, e: self.config["pins"].__setitem__(t, e))
        self.router.threads["a"] = {"model": "example-model", "effort": "medium"}
        self.router.supported["example-model"] = ["low", "medium", "high"]

    def request(self, text="复杂重构", **extra):
        return {"id": 1, "method": "turn/start", "params": {"threadId": "a", "input": [{"type": "text", "text": text}], **extra}}

    def test_arbitrary_catalog_model_maps_to_supported_effort(self):
        result = self.router.outgoing(self.request(model="example-model"))
        self.assertEqual(result["params"]["model"], "example-model")
        self.assertEqual(result["params"]["effort"], "high")

    def test_ceiling_with_sparse_catalog_never_upgrades(self):
        self.config["ceiling"] = "medium"
        self.router.supported["example-model"] = ["low", "high"]
        self.assertEqual(self.router.outgoing(self.request())["params"]["effort"], "low")
        self.router.supported["example-model"] = ["high"]
        request = self.request()
        self.assertIs(self.router.outgoing(request), request)

    def test_exact_model_filter_remains_backward_compatible(self):
        self.config["model"] = "another-model"
        request = self.request()
        self.assertIs(self.router.outgoing(request), request)

    def test_unknown_effort_catalog_passes_through(self):
        self.router.supported["example-model"] = ["minimal", "future-effort"]
        request = self.request()
        self.assertIs(self.router.outgoing(request), request)

    def test_unsupported_pins_are_not_silently_substituted(self):
        self.config["pins"]["a"] = "max"
        request = self.request()
        self.assertIs(self.router.outgoing(request), request)

    def test_notifications_do_not_route_or_create_pending_requests(self):
        request = self.request()
        del request["id"]
        self.assertIs(self.router.outgoing(request), request)
        self.assertFalse(self.router.pending)

    def test_second_start_before_server_notification_is_not_rerouted(self):
        self.router.outgoing(self.request())
        second = self.request("翻译")
        second["id"] = 2
        self.assertIs(self.router.outgoing(second), second)

    def test_app_server_argument_is_not_mistaken_for_subcommand(self):
        for args in (["exec", "app-server"], ["--config", "app-server", "exec", "hello"]):
            self.assertFalse(is_stdio_server(args))
        self.assertTrue(is_stdio_server(["--config=a=1", "app-server", "--listen=stdio://"]))

    def test_unknown_methods_do_not_accumulate_pending_metadata(self):
        for i in range(500):
            self.router.outgoing({"id": i, "method": "unknown/newMethod", "params": {}})
        self.assertFalse(self.router.pending)

    def test_catalog_refresh_drops_stale_models_and_accepts_pages(self):
        for ident, cursor, model in [(1, None, "first"), (2, "page-two", "second")]:
            self.router.outgoing({"id": ident, "method": "model/list", "params": {"cursor": cursor}})
            self.router.incoming({"id": ident, "result": {"data": [{"model": model, "supportedReasoningEfforts": [{"reasoningEffort": "high"}]}]}})
        self.assertEqual(set(self.router.supported), {"first", "second"})

    def test_disabled_settings_changes_never_pin(self):
        self.config["enabled"] = False
        self.router.outgoing({"id": 3, "method": "thread/settings/update", "params": {"threadId": "a", "effort": "high"}})
        self.router.incoming({"id": 3, "result": {}})
        self.assertFalse(self.config["pins"])

    def test_disabling_before_settings_acknowledgement_never_pins(self):
        self.router.outgoing({"id": 3, "method": "thread/settings/update", "params": {"threadId": "a", "effort": "high"}})
        self.config["enabled"] = False
        self.router.incoming({"id": 3, "result": {}})
        self.assertFalse(self.config["pins"])

    def test_manual_mode_precedence_and_future_efforts(self):
        request = {"id": 3, "method": "thread/settings/update", "params": {"threadId": "a", "effort": "high", "collaborationMode": {"mode": "plan", "settings": {"model": "example-model", "reasoning_effort": "future-effort"}}}}
        self.router.outgoing(request)
        self.router.incoming({"id": 3, "result": {}})
        self.assertEqual(self.config["pins"]["a"], "future-effort")

    def test_null_mode_effort_does_not_pin_ignored_top_level(self):
        self.router.outgoing({"id": 3, "method": "thread/settings/update", "params": {"threadId": "a", "effort": "high", "collaborationMode": {"mode": "plan", "settings": {"model": "example-model", "reasoning_effort": None}}}})
        self.router.incoming({"id": 3, "result": {}})
        self.assertFalse(self.config["pins"])

    def test_unknown_mode_is_rejected_by_router_before_rewriting(self):
        request = self.request(collaborationMode={"mode": "future", "settings": {"model": "example-model"}})
        before = copy.deepcopy(request)
        with self.assertRaises(ValueError):
            self.router.outgoing(request)
        self.assertEqual(request, before)
        self.assertFalse(self.router.decisions)

    def test_followup_with_attachment_does_not_keep_low(self):
        self.assertEqual(classify("继续", previous="low", attachment=True)[0], "high")

    def test_english_substrings_do_not_raise_translation_effort(self):
        self.assertEqual(classify("translate the latest prefix")[0], "low")

    def test_default_configs_do_not_share_mutable_pins(self):
        with tempfile.TemporaryDirectory() as temp:
            first = read_config(Path(temp))
            first["pins"]["a"] = "high"
            self.assertFalse(read_config(Path(temp))["pins"])


class PipeCompatibilityTests(unittest.TestCase):
    def run_proxy(self, server_code, payload, config=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server = root / "server.py"
            server.write_text(server_code, encoding="utf-8")
            if config is not None:
                (root / "config.json").write_text(config, encoding="utf-8")
            code = "from controller import proxy; from pathlib import Path; import sys; sys.exit(proxy([sys.argv[1]], Path(sys.executable), Path(sys.argv[2])))"
            result = subprocess.run([sys.executable, "-c", code, str(server), temp], input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            return result.stdout

    def test_simultaneous_large_input_output_and_exact_bytes(self):
        payload = b'{"text":"' + b'x' * 2_000_000 + b'"}\n'
        code = "import sys\nsys.stdout.buffer.write(b'y' * 2_000_000 + b'\\n')\nsys.stdout.buffer.flush()\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n"
        self.assertEqual(self.run_proxy(code, payload), b'y' * 2_000_000 + b'\n' + payload)

    def test_oversized_and_unterminated_frames_pass_through(self):
        payload = b'x' * 17_000_000 + b'\nunterminated'
        code = "import sys\nwhile True:\n data=sys.stdin.buffer.read(65536)\n if not data: break\n sys.stdout.buffer.write(data)\n sys.stdout.buffer.flush()\n"
        self.assertEqual(self.run_proxy(code, payload), payload)

    def test_malformed_protocol_and_bad_config_preserve_original_bytes(self):
        frames = [
            {"id": 2, "method": "model/list", "params": {}},
            {"id": 3, "method": "turn/start", "params": {"threadId": "a", "model": "x", "input": [{"type": "text", "text": "排查"}]}},
            {"id": [], "method": "turn/start", "params": {}},
            {"id": 4, "method": "turn/start", "params": []},
        ]
        payload = b'not-json\r\n' + b''.join((json.dumps(x) + '\n').encode() for x in frames)
        self.assertEqual(self.run_proxy("import sys\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n", payload, config="{broken"), payload)


if __name__ == "__main__":
    unittest.main()
