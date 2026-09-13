# Contributing

欢迎提交脱敏的规则误判、兼容性报告和聚焦修复。Chinese and English reports are welcome.

1. Describe expected and actual behavior with a minimal synthetic example.
2. Include macOS, Python and original Codex CLI versions when relevant.
3. Run `python3 -m unittest discover -v` before submitting code changes.
4. Keep offline, native App Server, and actual desktop evidence separate. Live probes are optional and use account quota when `--live` is supplied.

Never attach credentials, full conversations, personal paths, installation state or unredacted audit/probe logs. Replace thread IDs with placeholders. Preserve request contents, approval settings and original response behavior when changing the router.

This repository contains the community wrapper only. Do not contribute vendor application bundles, proprietary CLI binaries or copied application source.
