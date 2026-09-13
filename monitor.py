#!/usr/bin/env python3
"""Read-only local status feed. Never outputs prompts or raw session records."""
import argparse
import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time


def timestamp(value):
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, TypeError, ValueError):
        return 0


class Tail:
    """Incremental JSONL reader; bound startup and catch-up reads to 4 MiB."""
    def __init__(self, path):
        self.path, self.identity, self.offset = path, None, 0
        self.partial = b""

    def read(self, session=False):
        try:
            stat = self.path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if identity != self.identity or stat.st_size < self.offset:
                self.identity, self.offset, self.partial = identity, 0, b""
            start = max(self.offset, stat.st_size - 4 * 1024 * 1024)
            skipped = start > self.offset
            with self.path.open("rb") as stream:
                stream.seek(start)
                data = stream.read(4 * 1024 * 1024)
                self.offset = stream.tell()
            # A skipped prefix may start inside a JSON record.
            if skipped:
                self.partial = b""
                data = data.partition(b"\n")[2]
            lines = (self.partial + data).split(b"\n")
            self.partial = lines.pop()
            if len(self.partial) > 4 * 1024 * 1024:
                self.partial = b""
            for line in lines:
                if session and b'"turn_context"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if isinstance(entry, dict):
                    yield entry
        except OSError:
            return


class TaskNames:
    """Desktop names live in the append-only session index, separate from SQL titles."""
    def __init__(self, home):
        self.reader = Tail(home / "session_index.jsonl")
        self.names = {}

    def read(self):
        identity = self.reader.identity
        offset = self.reader.offset
        entries = list(self.reader.read())
        if identity != self.reader.identity or self.reader.offset < offset:
            self.names.clear()
        for entry in entries:
            thread, name = entry.get("id"), entry.get("thread_name")
            if not isinstance(thread, str) or not isinstance(name, str) or not name.strip():
                continue
            updated = timestamp(entry.get("updated_at"))
            if updated >= self.names.get(thread, (0, ""))[0]:
                self.names[thread] = (updated, name.strip())
        return {thread: value[1] for thread, value in self.names.items()}


def task_metadata(home, ids):
    """Only fetch titles and rollout paths; never query message columns."""
    if not ids:
        return {}
    databases = sorted(home.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in databases[:1]:
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
            try:
                placeholders = ",".join("?" for _ in ids)
                return {row[0]: {"title": row[1], "path": row[2]} for row in connection.execute(
                    "SELECT id, title, rollout_path FROM threads WHERE id IN (" + placeholders + ")", ids)}
            finally:
                connection.close()
        except (sqlite3.Error, OSError):
            pass
    return {}


class Monitor:
    def __init__(self, home):
        self.home = home.resolve()
        root = self.home / "effort-controller"
        self.audit = [Tail(root / "audit.previous.jsonl"), Tail(root / "audit.jsonl")]
        self.requests, self.sessions, self.contexts = {}, {}, {}
        self.task_names = TaskNames(self.home)

    def snapshot(self):
        events = [event for tail in self.audit for event in tail.read()]
        for event in sorted(events, key=lambda e: timestamp(e.get("time"))):
            thread, kind = event.get("thread"), event.get("event")
            if not isinstance(thread, str):
                continue
            old = self.requests.get(thread)
            if kind == "selected" and (not old or timestamp(event.get("time")) > timestamp(old.get("time"))):
                self.requests[thread] = dict(event, phase="selected", actual=None)
            elif old and event.get("pid") == old.get("pid") and timestamp(event.get("time")) >= timestamp(old.get("time")):
                if kind in ("accepted", "rejected"):
                    old["phase"] = kind
                    old["turn_id"] = event.get("turn_id")
                elif kind == "server_settings":
                    old.update(server_effort=event.get("effort"), server_time=event.get("time"))
        recent = sorted(self.requests, key=lambda t: timestamp(self.requests[t].get("time")), reverse=True)[:12]
        self.requests = {t: self.requests[t] for t in recent}
        metadata = task_metadata(self.home, recent)
        names = self.task_names.read()
        rows = []
        for thread in recent:
            request = self.requests[thread]
            task = metadata.get(thread, {})
            path = Path(task.get("path") or "/nonexistent")
            # A database path must stay inside this Codex home's session folders.
            allowed = any(base in path.resolve().parents for base in (self.home / "sessions", self.home / "archived_sessions"))
            if allowed:
                reader = self.sessions.get(thread)
                if reader is None or reader.path != path:
                    reader = self.sessions[thread] = Tail(path)
                for entry in reader.read(session=True):
                    payload = entry.get("payload", {})
                    if entry.get("type") == "turn_context" and isinstance(payload, dict):
                        context = {"time": entry.get("timestamp"), "effort": payload.get("effort"), "model": payload.get("model"), "turn_id": payload.get("turn_id")}
                        if timestamp(context["time"]) >= timestamp(self.contexts.get(thread, {}).get("time")):
                            self.contexts[thread] = context
            context = self.contexts.get(thread, {})
            title = names.get(thread) or task.get("title")
            if not isinstance(title, str) or not title.strip() or "# Files mentioned by the user:" in title:
                title = "任务名称暂不可用 · " + thread[-4:]
            row = {
                "thread": thread, "title": title,
                "requested": request.get("effort"), "original": request.get("original_effort"),
                "actual": None, "phase": request["phase"], "source": None,
                "time": request.get("time"), "actual_time": None,
                "reason": request.get("reason", ""), "model": request.get("model"),
            }
            if request["phase"] != "rejected":
                if context.get("effort") and timestamp(context.get("time")) >= timestamp(request.get("time")):
                    row.update(actual=context["effort"], phase="confirmed", source="服务端上下文", actual_time=context["time"], model=context.get("model"))
                    row["association"] = "本轮 ID 对应" if request.get("turn_id") and context.get("turn_id") == request["turn_id"] else "同任务最新上下文（按时间核对）"
                elif request.get("server_effort"):
                    row.update(actual=request["server_effort"], phase="confirmed", source="服务端设置回报", actual_time=request["server_time"])
                    row["association"] = "请求之后的设置回报"
            rows.append(row)
        self.sessions = {t: r for t, r in self.sessions.items() if t in recent}
        self.contexts = {t: c for t, c in self.contexts.items() if t in recent}
        try:
            config = json.loads((self.home / "effort-controller/config.json").read_text(encoding="utf-8"))
            enabled = config.get("enabled")
        except (OSError, ValueError, AttributeError):
            enabled = None
        return {"enabled": enabled, "tasks": rows, "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description="Read-only Codex effort status")
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    monitor = Monitor(args.codex_home)
    while True:
        try:
            print(json.dumps(monitor.snapshot(), ensure_ascii=False), flush=True)
        except BrokenPipeError:
            return
        if not args.watch:
            return
        time.sleep(1)


if __name__ == "__main__":
    main()
