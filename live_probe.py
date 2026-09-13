#!/usr/bin/python3
"""Opt-in smoke test against the original local Codex, with an ephemeral thread."""
import argparse
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
from install import find_cli

ROOT = Path(__file__).resolve().parent


def run(live, cli=None):
    real = find_cli(cli)
    with tempfile.TemporaryDirectory(prefix="effort-live-") as temp:
        code = "from controller import proxy; from pathlib import Path; import sys; sys.exit(proxy(['app-server'], Path(sys.argv[1]), Path(sys.argv[2])))"
        p = subprocess.Popen([sys.executable, "-u", "-c", code, str(real), temp], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        buffer = b""
        notes = []
        counter = 0
        def receive(timeout=30):
            nonlocal buffer
            deadline = time.monotonic() + timeout
            while b"\n" not in buffer:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([p.stdout], [], [], remaining)[0]:
                    raise TimeoutError("App Server response timeout")
                data = os.read(p.stdout.fileno(), 65536)
                if not data:
                    raise RuntimeError("App Server exited")
                buffer += data
            line, buffer = buffer.split(b"\n", 1)
            message = json.loads(line)
            if message.get("method") and "id" in message:
                # Never grant permissions or execute tools for this smoke test.
                p.stdin.write((json.dumps({"id": message["id"], "error": {"code": -32601, "message": "No tools or approvals in smoke test"}}) + "\n").encode())
            return message
        def call(method, params):
            nonlocal counter
            counter += 1
            ident = counter
            p.stdin.write((json.dumps({"id": ident, "method": method, "params": params}) + "\n").encode())
            while True:
                msg = receive()
                if msg.get("id") == ident:
                    if "error" in msg:
                        raise RuntimeError(method + ": " + str(msg["error"].get("code")))
                    return msg.get("result")
                notes.append(msg)
        report = {"version": subprocess.check_output([str(real), "--version"], text=True).strip(), "desktop_integration": "not_tested_by_this_probe", "tests": []}
        try:
            call("initialize", {"clientInfo": {"name": "effort_controller_probe", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
            p.stdin.write(b'{"method":"initialized"}\n')
            catalog = call("model/list", {"includeHidden": True})
            model = next(x for x in catalog["data"] if x["model"] == "gpt-6-astra")
            report["supported_efforts"] = [x["reasoningEffort"] for x in model["supportedReasoningEfforts"]]
            report["tests"].append({"name": "live_model_catalog", "passed": True})
            if live:
                started = call("thread/start", {"model": "gpt-6-astra", "ephemeral": True, "cwd": temp, "approvalPolicy": "never", "sandbox": "read-only", "baseInstructions": "This is a bounded transport test. Never use tools. Reply only OK.", "developerInstructions": "No tools, no files, no network. Output only OK."})
                thread = started["thread"]["id"]
                for text, expected in [("翻译：OK。请只输出 OK，不使用工具。", "low"), ("跨模块排查的传输测试。无需实际排查，只输出 OK，不使用工具。", "high")]:
                    notes.clear()
                    call("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}], "model": "gpt-6-astra", "effort": "medium"})
                    deadline = time.monotonic() + 90
                    completed = next((m for m in notes if m.get("method") == "turn/completed"), None)
                    while completed is None and time.monotonic() < deadline:
                        msg = receive(min(30, deadline - time.monotonic()))
                        if msg.get("method") == "turn/completed":
                            completed = msg
                    if completed is None:
                        call("turn/interrupt", {"threadId": thread, "turnId": "unknown"})
                        raise TimeoutError("Turn did not complete")
                    # The server emits settings on turn/start, before acknowledging it.
                    # The wrapper records this independently of its requested selection.
                    records = [json.loads(line) for line in (Path(temp) / "audit.jsonl").read_text().splitlines()]
                    settings = [r for r in records if r.get("event") == "server_settings" and r.get("thread") == thread]
                    actual = settings[-1].get("effort") if settings else None
                    status = completed.get("params", {}).get("turn", {}).get("status")
                    report["tests"].append({"name": "live_turn_" + expected, "requested_before_routing": "medium", "expected": expected, "server_effort_after_turn": actual, "turn_status": status, "passed": actual == expected and status == "completed"})
            report["audit"] = [json.loads(line) for line in (Path(temp) / "audit.jsonl").read_text().splitlines()]
        except Exception as error:
            report["error"] = type(error).__name__ + ": " + str(error)
        finally:
            audit_path = Path(temp) / "audit.jsonl"
            if audit_path.exists():
                report["audit"] = [json.loads(line) for line in audit_path.read_text().splitlines()]
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait()
        (ROOT / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report.get("error") or any(not t["passed"] for t in report["tests"]) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="Run two tiny real inference turns in an ephemeral read-only session")
    parser.add_argument("--cli", help="Path to the original desktop app's Codex CLI")
    args = parser.parse_args()
    sys.exit(run(args.live, args.cli))
