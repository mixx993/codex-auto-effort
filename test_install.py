import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import shlex
import subprocess

import install


class InstallTests(unittest.TestCase):
    def test_install_with_spaces_and_restore_original_environment(self):
        with tempfile.TemporaryDirectory(prefix="save tokens ") as temp:
            root = Path(temp)
            dest, plist = root / "installed", root / "agent.plist"
            real = root / "original codex"
            real.write_text("#!/bin/sh\nexit 0\n")
            real.chmod(0o700)
            environment = {"CODEX_APP_SERVER_FORCE_CLI": "0"}
            def fake(*args, **kw):
                if args[0] == "setenv":
                    environment[args[1]] = args[2]
                elif args[0] == "unsetenv":
                    environment.pop(args[1], None)
                return subprocess.CompletedProcess(args, 0, environment.get(args[1], "") if args[0] == "getenv" else "", "")
            with patch.object(install, "DEST", dest), patch.object(install, "PLIST", plist), patch.object(install, "launchctl", fake), patch.object(install.sys, "platform", "darwin"), contextlib.redirect_stdout(io.StringIO()):
                install.install(str(real))
                info = json.loads((dest / "installation.json").read_text())
                self.assertEqual(info["real_cli"], str(real.resolve()))
                self.assertEqual(info["previous_environment"], {"CODEX_CLI_PATH": None, "CODEX_APP_SERVER_FORCE_CLI": "0"})
                self.assertEqual(shlex.split((dest / "codex-wrapper").read_text().splitlines()[1])[2:4], [str(dest / "controller.py"), "--wrap"])
                result = subprocess.run([str(dest / "codex-effort"), "preview", "翻译"], capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout)["effort"], "low")
                install.install(str(real))
                self.assertEqual(json.loads((dest / "installation.json").read_text())["previous_environment"], info["previous_environment"])
                install.uninstall()
                self.assertEqual(environment, {"CODEX_APP_SERVER_FORCE_CLI": "0"})
                self.assertFalse(plist.exists())

    def test_missing_cli_has_no_environment_side_effects(self):
        with patch.object(install.sys, "platform", "darwin"), patch.object(install, "launchctl") as launch:
            with self.assertRaises(SystemExit):
                install.install("/nonexistent/savetokens-test/codex")
            launch.assert_not_called()

    def test_non_macos_install_stops_before_side_effects(self):
        with patch.object(install.sys, "platform", "linux"), patch.object(install, "launchctl") as launch:
            with self.assertRaises(SystemExit):
                install.install()
            launch.assert_not_called()


class UninstallTests(unittest.TestCase):
    def test_restore_only_owned_environment_preserve_later_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dest, plist = root / "installed", root / "agent.plist"
            dest.mkdir()
            (dest / "installation.json").write_text(json.dumps({"installed_by": install.LABEL, "previous_environment": {"CODEX_CLI_PATH": None, "CODEX_APP_SERVER_FORCE_CLI": "0"}}))
            (dest / "config.json").write_text('{"enabled":true,"pins":{"a":"high"}}')
            calls = []
            def fake(*args, **kw):
                calls.append(args)
                result = type("Result", (), {})()
                result.stdout = str(dest / "codex-wrapper") if args == ("getenv", "CODEX_CLI_PATH") else "later-user-change"
                return result
            with patch.object(install, "DEST", dest), patch.object(install, "PLIST", plist), patch.object(install, "launchctl", fake), contextlib.redirect_stdout(io.StringIO()):
                install.uninstall()
            self.assertIn(("unsetenv", "CODEX_CLI_PATH"), calls)
            self.assertNotIn(("setenv", "CODEX_APP_SERVER_FORCE_CLI", "0"), calls)
            config = json.loads((dest / "config.json").read_text())
            self.assertFalse(config["enabled"])
            self.assertEqual(config["pins"], {"a": "high"})

    def test_foreign_installation_is_not_modified(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "installation.json").write_text('{"installed_by":"other"}')
            with patch.object(install, "DEST", root), self.assertRaises(SystemExit):
                install.uninstall()


if __name__ == "__main__":
    unittest.main()
