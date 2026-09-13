#!/usr/bin/python3
"""Install a reversible per-user CLI wrapper; never modify the app bundle."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys

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
    return subprocess.run(["/bin/launchctl", *args], check=check, capture_output=True, text=True)


def install(cli=None):
    if sys.platform != "darwin":
        raise SystemExit("桌面启动接入目前仅支持 macOS。未修改环境。")
    real = find_cli(cli)
    if real == (DEST / "codex-wrapper").resolve() or real == (DEST / "controller.py").resolve():
        raise SystemExit("--cli 必须指向原版 CLI，不能指向包装程序。未修改环境。")
    info_path = DEST / "installation.json"
    if DEST.exists() and not info_path.exists():
        raise SystemExit("安装目录已存在且不属于此安装器，未覆盖。")
    if PLIST.exists() and not info_path.exists():
        raise SystemExit("启动配置名称已占用，未覆盖。")
    previous = {key: launchctl("getenv", key, check=False).stdout.strip() or None for key in KEYS}
    if info_path.exists():
        info = json.loads(info_path.read_text())
        if info.get("installed_by") != LABEL:
            raise SystemExit("已有安装记录不属于此安装器，未覆盖。")
        previous = info["previous_environment"]
    elif previous["CODEX_CLI_PATH"]:
        raise SystemExit("已有自定义 CODEX_CLI_PATH；请先确认如何与现有包装程序衔接。")
    DEST.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(SOURCE / "controller.py", DEST / "controller.py")
    shutil.copy2(SOURCE / "README.md", DEST / "README.md")
    info = {"real_cli": str(real), "previous_environment": previous, "source": str(SOURCE), "installed_by": LABEL}
    info_path.write_text(json.dumps(info, indent=2) + "\n")
    if not (DEST / "config.json").exists():
        (DEST / "config.json").write_text(json.dumps({"enabled": True, "model": "gpt-6-astra", "ceiling": "max", "pins": {}}, indent=2) + "\n")
    runner = DEST / "codex-wrapper"
    python = shlex.quote(sys.executable)
    runner.write_text("#!/bin/sh\nexec " + python + " " + shlex.quote(str(DEST / "controller.py")) + ' --wrap "$@"\n')
    runner.chmod(0o700)
    command = DEST / "codex-effort"
    command.write_text("#!/bin/sh\nexec " + python + " " + shlex.quote(str(DEST / "controller.py")) + ' "$@"\n')
    command.chmod(0o700)
    values = {"CODEX_CLI_PATH": str(runner), "CODEX_APP_SERVER_FORCE_CLI": "1"}
    env_script = DEST / "enable-environment.sh"
    env_script.write_text("#!/bin/sh\nset -eu\n" + "\n".join("/bin/launchctl setenv " + shlex.quote(k) + " " + shlex.quote(v) for k, v in values.items()) + "\n")
    env_script.chmod(0o700)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    plist = {"Label": LABEL, "ProgramArguments": ["/bin/sh", str(env_script)], "RunAtLoad": True}
    PLIST.write_bytes(plistlib.dumps(plist))
    domain = "gui/" + str(os.getuid())
    launchctl("bootout", domain + "/" + LABEL, check=False)
    result = launchctl("bootstrap", domain, str(PLIST), check=False)
    # Explicitly apply now; bootstrap schedules the next login too.
    for key, value in values.items():
        launchctl("setenv", key, value)
    verified = all(launchctl("getenv", k).stdout.strip() == v for k, v in values.items())
    print(json.dumps({"installed": str(DEST), "environment_verified": verified, "login_agent_loaded": result.returncode == 0, "desktop_restart_required": True}, ensure_ascii=False, indent=2))


def uninstall():
    path = DEST / "installation.json"
    if not path.exists():
        raise SystemExit("未找到此程序的安装记录。")
    info = json.loads(path.read_text())
    if info.get("installed_by") != LABEL:
        raise SystemExit("安装标记不符，未修改。")
    launchctl("bootout", "gui/" + str(os.getuid()) + "/" + LABEL, check=False)
    if PLIST.exists():
        value = plistlib.loads(PLIST.read_bytes())
        if value.get("Label") == LABEL:
            PLIST.unlink()
    expected = {"CODEX_CLI_PATH": str(DEST / "codex-wrapper"), "CODEX_APP_SERVER_FORCE_CLI": "1"}
    for key, previous in info["previous_environment"].items():
        current = launchctl("getenv", key, check=False).stdout.strip() or None
        if current != expected[key]:
            continue  # Preserve later user changes.
        if previous is None:
            launchctl("unsetenv", key)
        else:
            launchctl("setenv", key, previous)
    config_path = DEST / "config.json"
    config = json.loads(config_path.read_text())
    config["enabled"] = False
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print("已关闭自动选档并恢复原启动环境；代码和日志保留供审查。桌面应用下次启动后恢复原入口。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--cli", help="原版桌面应用内 Codex CLI 的路径（仅用于 install）")
    args = parser.parse_args()
    install(args.cli) if args.action == "install" else uninstall()
