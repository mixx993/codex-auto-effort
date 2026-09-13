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
from controller import LEVELS, automatic_effort

ROOT = Path(__file__).resolve().parent


def run(live, cli=None, model_name=None):
    real = find_cli(cli)
    with tempfile.TemporaryDirectory(prefix="effort-live-") as temp:
        code = "from controller import proxy; from pathlib import Path; import sys; sys.exit(proxy(['app-server'], Path(sys.argv[1]), Path(sys.argv[2])))"
        p = subprocess.Popen([sys.executable, "-u", "-c", code, str(real), temp], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        buffer = b""
        completed_turns = []
        counter = 0
        def receive(timeout=30):
            nonlocal buffer
            if timeout <= 0:
                raise TimeoutError("App Server response deadline exceeded")
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
            deadline = time.monotonic() + 30
            while True:
                msg = receive(max(0, deadline - time.monotonic()))
                if msg.get("id") == ident:
                    if "error" in msg:
                        raise RuntimeError(method + ": " + str(msg["error"].get("code")))
                    return msg.get("result")
                if msg.get("method") == "turn/completed":
                    completed_turns.append(msg)
        report = {"desktop_integration": "not_tested_by_this_probe", "tests": []}
        try:
            report["version"] = subprocess.check_output([str(real), "--version"], text=True, timeout=10).strip()
            call("initialize", {"clientInfo": {"name": "effort_controller_probe", "version": "0.2.0"}, "capabilities": {"experimentalApi": True}})
            p.stdin.write(b'{"method":"initialized"}\n')
            models = []
            cursor = None
            seen = set()
            for _ in range(100):
                params = {"includeHidden": True}
                if cursor:
                    params["cursor"] = cursor
                catalog = call("model/list", params)
                models.extend(catalog["data"])
                cursor = catalog.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise RuntimeError("Repeated model catalog cursor")
                seen.add(cursor)
            else:
                raise RuntimeError("Model catalog pagination limit exceeded")
            report["models"] = [{"model": x["model"], "supported_efforts": [e["reasoningEffort"] for e in x["supportedReasoningEfforts"]]} for x in models]
            report["tests"].append({"name": "live_model_catalog", "passed": True})
            if live:
                model = next((x for x in report["models"] if x["model"] == model_name), None)
                if not model:
                    raise ValueError("Requested model is absent from the catalog")
                choices = model["supported_efforts"]
                if automatic_effort("high", choices, "max") is None:
                    raise ValueError("Model has no effort compatible with the low/high probe")
                started = call("thread/start", {"model": model_name, "ephemeral": True, "cwd": temp, "approvalPolicy": "never", "sandbox": "read-only", "baseInstructions": "This is a bounded transport test. Never use tools. Reply only OK.", "developerInstructions": "No tools, no files, no network. Output only OK."})
                thread = started["thread"]["id"]
                for text, expected in [("翻译：OK。请只输出 OK，不使用工具。", "low"), ("跨模块排查的传输测试。无需实际排查，只输出 OK，不使用工具。", "high")]:
                    expected = automatic_effort(expected, choices, "max")
                    if expected is None:
                        continue
                    before = next((e for e in choices if e in LEVELS and e != expected), expected)
                    completed_turns.clear()
                    turn = call("turn/start", {"threadId": thread, "input": [{"type": "text", "text": text}], "model": model_name, "effort": before})
                    deadline = time.monotonic() + 90
                    completed = next(iter(completed_turns), None)
                    while completed is None and time.monotonic() < deadline:
                        msg = receive(min(30, deadline - time.monotonic()))
                        if msg.get("method") == "turn/completed":
                            completed = msg
                    if completed is None:
                        call("turn/interrupt", {"threadId": thread, "turnId": turn["turn"]["id"]})
                        raise TimeoutError("Turn did not complete")
                    # The server emits settings on turn/start, before acknowledging it.
                    # The wrapper records this independently of its requested selection.
                    records = [json.loads(line) for line in (Path(temp) / "audit.jsonl").read_text().splitlines()]
                    settings = [r for r in records if r.get("event") == "server_settings" and r.get("thread") == thread]
                    actual = settings[-1].get("effort") if settings else None
                    status = completed.get("params", {}).get("turn", {}).get("status")
                    report["tests"].append({"name": "live_turn_" + expected, "requested_before_routing": before, "expected": expected, "server_effort_after_turn": actual, "turn_status": status, "passed": actual == expected and status == "completed"})

        except Exception as error:
            report["error"] = type(error).__name__ + ": " + str(error)
        finally:
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
    parser.add_argument("--model", help="Model ID for optional live inference (required with --live)")
    args = parser.parse_args()
    if args.live and not args.model:
        parser.error("--live requires --model; the probe never chooses a model for you")
    sys.exit(run(args.live, args.cli, args.model))
