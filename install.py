#!/usr/bin/python3
"""Install a reversible per-user CLI wrapper; never modify the app bundle."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys

from controller import DEFAULTS, atomic_json, atomic_write, edit_config, read_config

SOURCE = Path(__file__).resolve().parent
DEST = Path.home() / ".codex" / "effort-controller"
LABEL = "local.codex.effort-environment"
PLIST = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
KEYS = ("CODEX_CLI_PATH", "CODEX_APP_SERVER_FORCE_CLI")


def find_cli(explicit=None):
    candidates = [Path(explicit).expanduser()] if explicit else [
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path("/Applications/Codex.app/Contents/Resources/codex"),
        Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
        Path.home() / "Applications/Codex.app/Contents/Resources/codex",
    ]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise SystemExit("找不到可执行的原版 Codex CLI；请用 --cli 指定应用内的 CLI 路径。未修改环境。")


def launchctl(*args, check=True):
    return subprocess.run(["/bin/launchctl", *args], check=check, capture_output=True, text=True, timeout=15)


def install(cli=None):
    if sys.platform != "darwin":
        raise SystemExit("桌面启动接入目前仅支持 macOS。未修改环境。")
    real = find_cli(cli)
    if real == (DEST / "codex-wrapper").resolve() or real == (DEST / "controller.py").resolve():
        raise SystemExit("--cli 必须指向原版 CLI，不能指向包装程序。未修改环境。")
    info_path = DEST / "installation.json"
    runner = DEST / "codex-wrapper"
    env_script = DEST / "enable-environment.sh"
    values = {"CODEX_CLI_PATH": str(runner), "CODEX_APP_SERVER_FORCE_CLI": "1"}
    current = {key: launchctl("getenv", key, check=False).stdout.strip() or None for key in KEYS}
    if DEST.exists() and not info_path.exists():
        raise SystemExit("安装目录已存在且不属于此安装器，未覆盖。")
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    if info and info.get("installed_by") != LABEL:
        raise SystemExit("已有安装记录不属于此安装器，未覆盖。")
    if PLIST.exists() and (not info or not owns_plist()):
        raise SystemExit("启动配置名称已占用，未覆盖。")
    active = info.get("active", current["CODEX_CLI_PATH"] == str(runner))
    previous = info["previous_environment"] if active else current
    if current["CODEX_CLI_PATH"] not in (None, str(runner)):
        raise SystemExit("已有其他自定义 CODEX_CLI_PATH，未覆盖。")
    if active and any(current[k] not in (values[k], previous[k]) for k in KEYS):
        raise SystemExit("安装后启动环境已被其他程序修改，未覆盖。")
    if (DEST / "config.json").exists():
        read_config(DEST)  # Validate before touching the installation.
    python = shlex.quote(sys.executable)
    script = "#!/bin/sh\nexec " + python + " " + shlex.quote(str(DEST / "controller.py"))
    files = {
        DEST / "controller.py": ((SOURCE / "controller.py").read_bytes(), 0o600),
        DEST / "README.md": ((SOURCE / "README.md").read_bytes(), 0o600),
        runner: ((script + ' --wrap "$@"\n').encode(), 0o700),
        DEST / "codex-effort": ((script + ' "$@"\n').encode(), 0o700),
        env_script: (("#!/bin/sh\nset -eu\n" + "\n".join("/bin/launchctl setenv " + shlex.quote(k) + " " + shlex.quote(v) for k, v in values.items()) + "\n").encode(), 0o700),
        PLIST: (plistlib.dumps({"Label": LABEL, "ProgramArguments": ["/bin/sh", str(env_script)], "RunAtLoad": True}), 0o600),
        info_path: ((json.dumps({"real_cli": str(real), "previous_environment": previous, "source": str(SOURCE), "installed_by": LABEL, "active": True}, indent=2) + "\n").encode(), 0o600),
    }
    if not (DEST / "config.json").exists():
        files[DEST / "config.json"] = ((json.dumps(DEFAULTS, indent=2) + "\n").encode(), 0o600)
    backup = {path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.exists() else None for path in files}
    domain = "gui/" + str(os.getuid())
    was_loaded = launchctl("print", domain + "/" + LABEL, check=False).returncode == 0
    if was_loaded and not PLIST.exists():
        raise SystemExit("同名登录服务已存在，但没有可验证的配置，未修改。")
    touched_environment = False
    try:
        DEST.mkdir(parents=True, exist_ok=True, mode=0o700)
        for path, (data, mode) in files.items():
            atomic_write(path, data, mode)
        touched_environment = True
        launchctl("bootout", domain + "/" + LABEL, check=False)
        launchctl("bootstrap", domain, str(PLIST))
        for key, value in values.items():
            launchctl("setenv", key, value)
        if any(launchctl("getenv", k).stdout.strip() != v for k, v in values.items()):
            raise RuntimeError("启动环境校验失败")
    except Exception as error:
        failures = []
        def restore(action):
            try:
                action()
            except Exception as rollback_error:
                failures.append(type(rollback_error).__name__)
        if touched_environment:
            restore(lambda: launchctl("bootout", domain + "/" + LABEL, check=False))
        for path, saved in backup.items():
            restore(lambda path=path, saved=saved: atomic_write(path, *saved) if saved else path.unlink(missing_ok=True))
        if touched_environment:
            if was_loaded:
                restore(lambda: launchctl("bootstrap", domain, str(PLIST)))
            for key, value in current.items():
                restore(lambda key=key, value=value: set_environment(key, value))
        if not info:
            try:
                DEST.rmdir()
            except OSError:
                pass
        suffix = "；回滚未完全成功，请检查启动环境" if failures else "；已回滚此次改动"
        raise SystemExit(type(error).__name__ + suffix) from error
    print(json.dumps({"installed": str(DEST), "environment_verified": True, "login_agent_loaded": True, "desktop_restart_required": True}, ensure_ascii=False, indent=2))


def set_environment(key, value):
    return launchctl("unsetenv", key) if value is None else launchctl("setenv", key, value)


def owns_plist():
    value = plistlib.loads(PLIST.read_bytes())
    return value.get("Label") == LABEL and value.get("ProgramArguments") == ["/bin/sh", str(DEST / "enable-environment.sh")]


def uninstall():
    path = DEST / "installation.json"
    if not path.exists():
        raise SystemExit("未找到此程序的安装记录。")
    info = json.loads(path.read_text())
    if info.get("installed_by") != LABEL:
        raise SystemExit("安装标记不符，未修改。")
    if PLIST.exists() and not owns_plist():
        raise SystemExit("登录配置已被其他程序替换，未修改。")
    edit_config(lambda config: config.update(enabled=False), DEST)
    launchctl("bootout", "gui/" + str(os.getuid()) + "/" + LABEL, check=False)
    if PLIST.exists():
        PLIST.unlink()
    expected = {"CODEX_CLI_PATH": str(DEST / "codex-wrapper"), "CODEX_APP_SERVER_FORCE_CLI": "1"}
    for key in KEYS:
        current = launchctl("getenv", key, check=False).stdout.strip() or None
        if current == expected[key]:
            set_environment(key, info["previous_environment"][key])
    info["active"] = False
    atomic_json(path, info)
    print("已关闭自动选档并恢复原启动环境；代码和日志保留供审查。桌面应用下次启动后恢复原入口。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--cli", help="原版桌面应用内 Codex CLI 的路径（仅用于 install）")
    args = parser.parse_args()
    install(args.cli) if args.action == "install" else uninstall()
