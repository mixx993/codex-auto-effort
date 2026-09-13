#!/usr/bin/python3
"""Local, credential-free JSONL middleware for the desktop's Codex CLI."""
import argparse
import copy
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
DEFAULTS = {"enabled": True, "model": "gpt-6-astra", "ceiling": "max", "pins": {}}
LEVELS = ["low", "medium", "high", "xhigh", "max", "ultra"]


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_config(root=ROOT):
    p = root / "config.json"
    value = json.loads(p.read_text()) if p.exists() else {}
    config = dict(DEFAULTS, **value)
    if type(config["enabled"]) is not bool or config["ceiling"] not in LEVELS[:-1]:
        raise ValueError("invalid config")
    if not isinstance(config["pins"], dict) or any(v not in LEVELS for v in config["pins"].values()):
        raise ValueError("invalid pins")
    return config


def edit_config(change, root=ROOT):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "config.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = read_config(root)
        change(config)
        atomic_json(root / "config.json", config)


def clean_prompt(text):
    # Attached/context documents are data, not routing commands.
    text = re.sub(r"<(environment_context|INSTRUCTIONS|instructions|system_reminder|recommended_plugins|app-context)\b[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def classify(text, previous=None, attachment=False):
    text = clean_prompt(text).strip()
    explicit = re.search(r"(?:^|\n)\s*(?:请)?(?:将)?(?:推理(?:强度|档位)|reasoning effort)\s*(?:设为|设置为|使用|[:：=])\s*(low|medium|high|xhigh|max|ultra)\s*[。.!！]?\s*(?:$|\n)", text, re.I)
    if explicit:
        return explicit[1].lower(), "用户明确指定", True
    if re.fullmatch(r"(?:好的?[，, ]*)?(?:继续|继续吧|接着做|开始吧|执行|可以|没问题|continue|go ahead)[。.!！\s]*", text, re.I):
        return previous or "medium", "延续上一轮任务档位", False
    rules = [
        ("max", r"开放性难题|长期未解决|形式化证明|证明.{0,12}(?:猜想|定理)|open research problem|prove.{0,20}theorem", "开放性推理或形式化证明"),
        ("xhigh", r"分布式.{0,12}(?:一致性|死锁)|复杂重构|系统架构(?:设计|决策)|反复.{0,12}(?:失败|未解决)|数据(?:损坏|丢失|完整性)|安全漏洞|竞争条件|race condition|deadlock|data corruption", "疑难故障、架构或数据完整性"),
        ("high", r"跨模块|架构|未知原因|原因不明|排查|性能瓶颈|交叉核对|多份.{0,12}(?:核对|对比)|图纸.{0,12}(?:核对|建模)|(?:平面|立面).{0,24}(?:结构|核对)|重构|方案.{0,10}(?:比较|对比|取舍)|设计.{0,8}(?:系统|产品)|debug|investigate|cross.module|refactor", "需要排查、跨模块或多资料推理"),
        ("medium", r"开发|实现|增加|添加|修复|测试|构建|编写|生成|制作|分析|开发|implement|build|create|fix|test|analy[sz]e", "常规实现或分析"),
        ("low", r"翻译|改名|重命名|格式(?:化|调整)|润色|解释.{0,10}(?:这句|这行|命令)|现在几点|今天几号|你好|translate|rename|reformat|hello", "明确的简单任务"),
    ]
    for effort, pattern, reason in rules:
        if re.search(pattern, text, re.I):
            if attachment and LEVELS.index(effort) < LEVELS.index("high"):
                return "high", "附带非文本资料，采用保守档位", False
            return effort, reason, False
    if attachment:
        return "high", "附带非文本资料，采用保守档位", False
    return "medium", "信息不足，使用日常起点", False


class Router:
    def __init__(self, config=read_config, audit=lambda event: None, pin=lambda thread, effort: None):
        self.config, self.audit, self.pin = config, audit, pin
        self.pending, self.threads, self.supported, self.previous = {}, {}, {}, {}
        self.decisions = {}
        self.active = set()

    def outgoing(self, message):
        if not isinstance(message, dict):
            return message
        method, p = message.get("method"), message.get("params") or {}
        if not isinstance(p, dict):
            return message
        ident, thread = message.get("id"), p.get("threadId")
        if ident is not None and method:
            # Track only protocol metadata; never persist prompt bodies.
            self.pending[ident] = (method, thread)
        if method == "thread/settings/update" and thread:
            effort = p.get("effort") or (p.get("collaborationMode") or {}).get("settings", {}).get("reasoning_effort")
            if effort in LEVELS and effort != self.threads.get(thread, {}).get("effort"):
                self.pending[ident] = (method, thread, effort)
        if method != "turn/start" or not thread or p.get("toolOutput") is not None:
            return message
        if thread in self.active:
            self.audit({"event": "skipped", "thread": thread, "reason": "active_turn_steering"})
            return message
        inputs = p.get("input")
        if not isinstance(inputs, list) or not inputs:
            return message
        texts = [item.get("text", "") for item in inputs if isinstance(item, dict) and item.get("type") == "text"]
        text = "\n".join(t for t in texts if isinstance(t, str))
        attachment = any(isinstance(item, dict) and item.get("type") in ("image", "localImage", "mention") for item in inputs)
        if not text.strip() and not attachment:
            return message
        config = self.config()
        if not config["enabled"]:
            return message
        mode = (p.get("collaborationMode") or {}).get("settings") or {}
        model = mode.get("model") or p.get("model") or self.threads.get(thread, {}).get("model")
        if model != config["model"]:
            return message
        choices = self.supported.get(model)
        if not choices:
            self.audit({"event": "skipped", "thread": thread, "reason": "model_catalog_unavailable"})
            return message
        effort, reason, explicit = classify(text, self.previous.get(thread), attachment)
        pinned = config["pins"].get(thread)
        if pinned and not explicit:
            effort, reason = pinned, "用户固定档位"
        if not explicit and not pinned:
            effort = LEVELS[min(LEVELS.index(effort), LEVELS.index(config["ceiling"]))]
        if effort not in choices:
            # No silent substitution of a user's explicit selection.
            self.audit({"event": "skipped", "thread": thread, "reason": "unsupported_effort", "requested": effort})
            return message
        result = copy.deepcopy(message)
        result["params"]["effort"] = effort
        if mode:
            result["params"]["collaborationMode"]["settings"]["reasoning_effort"] = effort
        self.decisions[ident] = {"thread": thread, "effort": effort, "reason": reason}
        self.audit({"event": "selected", **self.decisions[ident]})
        return result

    def incoming(self, message):
        if not isinstance(message, dict):
            return
        p, method = message.get("params") or {}, message.get("method")
        if method == "thread/settings/updated":
            thread, settings = p.get("threadId"), p.get("threadSettings", {})
            self.threads[thread] = settings
            self.audit({"event": "server_settings", "thread": thread, "model": settings.get("model"), "effort": settings.get("effort")})
        if method == "turn/started":
            self.active.add(p.get("threadId"))
        if method == "turn/completed":
            self.active.discard(p.get("threadId"))
        # JSON-RPC peers have independent request-id namespaces. A server tool
        # request must not consume a pending client request with the same id.
        if method:
            return
        ident = message.get("id")
        pending = self.pending.pop(ident, None)
        if pending is None:
            return
        method, thread = pending[:2]
        result = message.get("result")
        decision = self.decisions.pop(ident, None)
        if decision:
            accepted = "error" not in message and isinstance(result, dict)
            if accepted:
                self.previous[decision["thread"]] = decision["effort"]
            self.audit({"event": "accepted" if accepted else "rejected", **decision})
        if not isinstance(result, dict) or "error" in message:
            return
        if method == "model/list":
            for model in result.get("data", []):
                self.supported[model.get("model")] = [x["reasoningEffort"] for x in model.get("supportedReasoningEfforts", []) if x.get("reasoningEffort") in LEVELS]
        if method in ("thread/start", "thread/resume", "thread/fork"):
            thread = result.get("thread", {}).get("id")
            self.threads[thread] = {"model": result.get("model"), "effort": result.get("reasoningEffort")}
            effort = result.get("reasoningEffort")
            if effort in LEVELS:
                self.previous[thread] = effort
            if result.get("thread", {}).get("status", {}).get("type") == "active":
                self.active.add(thread)
        if method == "thread/settings/update" and len(pending) == 3:
            self.pin(thread, pending[2])
            self.audit({"event": "manual_pin", "thread": thread, "effort": pending[2]})


def audit(event, root=ROOT):
    try:
        root.mkdir(parents=True, exist_ok=True)
        with (root / "audit.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = root / "audit.jsonl"
            if path.exists() and path.stat().st_size > 2_000_000:
                os.replace(path, root / "audit.previous.jsonl")
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a") as stream:
                stream.write(json.dumps({"time": datetime.datetime.now(datetime.timezone.utc).isoformat(), "pid": os.getpid(), **event}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # Logging failure must never break a user turn.


def proxy(args, real, root=ROOT):
    env = dict(os.environ)
    # Prevent recursive wrapper selection by child CLI helpers.
    env["CODEX_CLI_PATH"] = str(real)
    child = subprocess.Popen([str(real), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, bufsize=0)
    router = Router(lambda: read_config(root), lambda e: audit(e, root), lambda t, e: edit_config(lambda c: c["pins"].__setitem__(t, e), root))
    audit({"event": "proxy_started", "child_pid": child.pid}, root)
    def stop(signum, frame):
        if child.poll() is None:
            child.send_signal(signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.buffer, selectors.EVENT_READ, "client")
    selector.register(child.stdout, selectors.EVENT_READ, "server")
    buffers = {"client": b"", "server": b""}
    try:
        while selector.get_map():
            for key, _ in selector.select(1):
                side = key.data
                data = os.read(key.fd, 65536)
                if not data:
                    selector.unregister(key.fileobj)
                    # Preserve an unterminated final frame unchanged.
                    if buffers[side]:
                        target = child.stdin if side == "client" else sys.stdout.buffer
                        target.write(buffers[side]); target.flush()
                        buffers[side] = b""
                    if side == "client":
                        child.stdin.close()
                    else:
                        return child.wait()
                    continue
                buffers[side] += data
                while b"\n" in buffers[side]:
                    line, buffers[side] = buffers[side].split(b"\n", 1)
                    outgoing = line + b"\n"
                    try:
                        message = json.loads(line)
                        if side == "client":
                            routed = router.outgoing(message)
                            if routed is not message:
                                outgoing = json.dumps(routed, ensure_ascii=False).encode() + b"\n"
                        else:
                            router.incoming(message)
                    except Exception as error:
                        audit({"event": "passthrough_error", "error_type": type(error).__name__}, root)
                    target = child.stdin if side == "client" else sys.stdout.buffer
                    target.write(outgoing); target.flush()
            if child.poll() is not None and not selector.get_map():
                break
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        selector.close()
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
    return child.returncode or 0


def is_stdio_server(args):
    if "app-server" not in args:
        return False
    tail = args[args.index("app-server") + 1:]
    if any(x in tail for x in ("proxy", "daemon", "generate-ts", "generate-json-schema", "--help", "-h")):
        return False
    for i, arg in enumerate(tail):
        if arg == "--listen" and (i + 1 == len(tail) or tail[i + 1] != "stdio://"):
            return False
        if arg.startswith("--listen=") and arg != "--listen=stdio://":
            return False
    return True


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--wrap":
        args = sys.argv[2:]
        install = json.loads((ROOT / "installation.json").read_text())
        real = Path(install["real_cli"])
        if real.resolve() == Path(sys.argv[0]).resolve():
            raise RuntimeError("recursive CLI path")
        if is_stdio_server(args):
            return proxy(args, real)
        os.execv(str(real), [str(real), *args])
    parser = argparse.ArgumentParser(description="Codex 桌面自动推理档位控制")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "on", "off"):
        sub.add_parser(name)
    p = sub.add_parser("preview"); p.add_argument("text")
    p = sub.add_parser("pin"); p.add_argument("thread"); p.add_argument("effort", choices=LEVELS)
    p = sub.add_parser("auto"); p.add_argument("thread", help="会话 ID，或 all 清除全部固定档位")
    p = sub.add_parser("ceiling"); p.add_argument("effort", choices=LEVELS[:-1])
    args = parser.parse_args()
    if args.command == "preview":
        effort, reason, _ = classify(args.text)
        print(json.dumps({"effort": effort, "reason": reason, "mode": "local_rule_preview"}, ensure_ascii=False))
        return 0
    if args.command in ("on", "off"):
        edit_config(lambda c: c.update(enabled=args.command == "on"))
    if args.command == "pin":
        edit_config(lambda c: c["pins"].__setitem__(args.thread, args.effort))
    if args.command == "auto":
        edit_config(lambda c: c["pins"].clear() if args.thread == "all" else c["pins"].pop(args.thread, None))
    if args.command == "ceiling":
        edit_config(lambda c: c.update(ceiling=args.effort))
    print(json.dumps(read_config(), ensure_ascii=False, indent=2))
    path = ROOT / "audit.jsonl"
    if path.exists():
        print("最近记录：")
        for line in path.read_text().splitlines()[-8:]:
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
