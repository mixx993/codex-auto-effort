#!/usr/bin/env python3
"""Build an optional read-only macOS status app; no login item or routing changes."""
import argparse
import json
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parent


def build(destination):
    if sys.platform != "darwin":
        raise SystemExit("菜单栏应用仅支持 macOS。")
    if destination.exists():
        raise SystemExit("输出应用已存在；请使用 --output 选择新路径，避免覆盖运行中的应用。")
    subprocess.run(["xcrun", "--find", "swiftc"], check=True, stdout=subprocess.DEVNULL)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="effort-menubar-") as temp:
        app = Path(temp) / destination.name
        contents = app / "Contents"
        binary = contents / "MacOS"
        resources = contents / "Resources"
        binary.mkdir(parents=True)
        resources.mkdir()
        target = platform.machine() + "-apple-macosx13.0"
        subprocess.run(["xcrun", "swiftc", "-O", "-target", target, str(SOURCE / "menubar.swift"), "-framework", "AppKit", "-o", str(binary / "CodexAutoEffort")], check=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "io.github.mixx993.codex-auto-effort",
            "CFBundleName": "Codex Auto Effort",
            "CFBundleExecutable": "CodexAutoEffort",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.3.0",
            "CFBundleVersion": "1",
            "LSUIElement": True,
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
        }))
        shutil.copy2(SOURCE / "monitor.py", resources / "monitor.py")
        (resources / "runtime.json").write_text(json.dumps({"python": sys.executable}) + "\n", encoding="utf-8")
        subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
        shutil.copytree(app, destination)
    print(str(destination))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path.home() / "Applications" / "Codex Auto Effort.app")
    args = parser.parse_args()
    build(args.output.expanduser().resolve())
