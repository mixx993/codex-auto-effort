# Codex Auto Effort

Experimental local reasoning-effort routing for the Codex desktop app on macOS.

[中文说明](README.md) · [MIT License](LICENSE)

A Python standard-library JSONL wrapper sits between the desktop client and its original App Server. Before a new `turn/start`, local text rules choose a reasoning effort. It reuses the original CLI and login; no additional API key or classification service is required.

This is an independent community project, not affiliated with or endorsed by OpenAI. There is no measured token, cost, or quality benchmark and no promised savings. No vendor application or CLI binaries are distributed.

## Scope

- New installations route any model whose effort levels have been advertised by `model/list`. Model selection is never changed. Existing exact model filters are preserved on upgrade.
- Automatic selection uses the highest advertised known level at or below both the rule target and ceiling. Unsupported explicit selections and pins pass through without substitution.
- Rules map requests to `low`, `medium`, `high`, `xhigh`, or `max`; `ultra` is never selected automatically.
- Supports per-thread pins, a configurable automatic ceiling, and an on/off switch.
- Successful manual effort changes pin that thread. Explicit standalone prompt instructions can override a pin for one turn.
- Changes effort fields only, including the collaboration-mode override; preserves messages, tools, permissions and server responses.
- It does not switch effort during generation, inspect image contents, or understand the entire conversation. Known context tags and quoted blocks receive limited filtering, not a semantic security boundary.
- The desktop launch environment hooks are undocumented implementation details. Compatibility can break after app updates.

## Install

Requires macOS, Python 3.9+, and a compatible installed and authenticated desktop app. Windows is unsupported because this implementation uses Unix pipes and file locks. Linux can run the protocol wrapper and offline tests, but has no desktop integration installer here.

```sh
git clone https://github.com/mixx993/codex-auto-effort.git
cd codex-auto-effort
python3 install.py install
```

The installer searches system/user Applications directories for ChatGPT.app or Codex.app. Use `--cli "/path/to/Codex.app/Contents/Resources/codex"` for a different location. Path detection is not a compatibility guarantee.

Installs to `~/.codex/effort-controller`, sets per-user `CODEX_CLI_PATH` and `CODEX_APP_SERVER_FORCE_CLI`, and adds a login LaunchAgent. It preserves the original environment and does not modify the app bundle or global config. Keep the Python interpreter used for installation available. Normal installation failures roll back files, the login service and launch environment; rollback errors are reported. Power loss and forced process termination are outside that guarantee.

Wait for current work to finish, then quit and reopen the desktop app normally. The installer never force-quits it. Verify audit events; installation success alone does not prove that desktop requests use the wrapper.

```sh
~/.codex/effort-controller/codex-effort status
~/.codex/effort-controller/codex-effort preview 'investigate a cross-module failure'
~/.codex/effort-controller/codex-effort ceiling high
~/.codex/effort-controller/codex-effort model '*'
~/.codex/effort-controller/codex-effort model MODEL_ID
~/.codex/effort-controller/codex-effort pin THREAD_ID high
~/.codex/effort-controller/codex-effort auto THREAD_ID
~/.codex/effort-controller/codex-effort off
~/.codex/effort-controller/codex-effort on
```

The existing install directory, command name and LaunchAgent label are retained for backward compatibility. To opt an older exact-model installation into generic routing, run `model '*'`. This filter does not switch models.

`preview` is text classification only, not a check of effective pins, model support or desktop integration. To specify one turn explicitly, use a standalone line such as `reasoning effort: high`.

## Optional menu bar status

The desktop model selector may retain its manual value after the wrapper rewrites a request. A separate read-only menu bar app shows compact status such as `M ✓` (L / M / H / XH / MAX / UL):

```sh
python3 build_menubar.py
open "$HOME/Applications/Codex Auto Effort.app"
```

Requires macOS 13+, the Swift compiler from Xcode Command Line Tools, and the Python interpreter used to build it. This builds an ad-hoc-signed local app, not a notarized binary distribution.

Colors encode effort: L green, M blue, H orange, XH purple, MAX pink, UL red. A legend is available under More. Symbols encode confirmation independently; disabled/disconnected monitoring uses the default system tint.

`✓` means a server context/settings record was found; `…` means selected or accepted but unconfirmed; `!` means rejected; `OFF` means routing disabled; `?` means monitoring unavailable. The compact menu highlights effort, confirmation and a single-line task title. Details and task selection live in submenus. It follows the most recent routed task or a manually selected task, not the foreground chat, and does not imply a turn is still running.

Task names prefer the local `session_index.jsonl` desktop names and follow rename records; database titles are a fallback, excluding attachment boilerplate.

The helper reads local audit records, the task-name index, task titles/rollout paths and `turn_context` metadata. It does not emit prompts, change routing, or make model requests. Legacy logs use task/time correlation; newer logs include turn IDs. Missing/changed data leaves the result unconfirmed. Startup and catch-up session reads are limited to 4 MiB; older contexts beyond that window may be unavailable.

Works with the older installed wrapper without restarting Codex. No login item is installed. Quitting the menu app leaves routing running; delete its `.app` to remove the display. The builder refuses to overwrite an existing app; use `--output` for a new destination. For headless status use `python3 monitor.py` or add `--watch`.

## Uninstall integration

From the cloned repository:

```sh
python3 install.py uninstall
```

Disables routing, unloads the login agent, and restores owned environment values while preserving later user changes. Code and logs remain for inspection; the original desktop entry point resumes on its next launch. Reinstalling preserves config, including an existing disabled state; use `codex-effort on` to re-enable it.

## Verification and privacy

```sh
python3 -m unittest discover -v  # Offline rules, transport and mocked installation
python3 live_probe.py          # Local App Server/catalog check; no inference
python3 live_probe.py --live --model MODEL_ID  # Optional real inference, uses account quota
```

See [verification scope](docs/verification.md). The probe creates ignored local `verification.json` with versions, catalog metadata and test results, without raw audit records. Live inference requires an explicit model; the probe never chooses one for you. It reads all catalog pages. Review any report before publishing.

Audit logs omit prompts, attachments, tool arguments and credentials, but include thread IDs, times and routing metadata. `selected` means rewritten, `accepted` means accepted by the server, and `server_settings` records reported settings; none proves answer quality. Logs rotate at approximately two 2 MB files. The wrapper adds no external service; the original CLI still performs its normal network communication.

Do not upload private logs, credentials or conversation screenshots in issues. See [contribution guidance](CONTRIBUTING.md).

See the [compatibility review](docs/compatibility.md) for fixed issues and remaining limitations.
