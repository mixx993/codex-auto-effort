# Codex Auto Effort

**为 Codex 桌面端按任务选择推理强度的本地实验性控制程序。**

[English](README.en.md) · [MIT License](LICENSE) · [验证范围](docs/verification.md)

简单请求使用较低档位，复杂任务提高档位；支持手动固定和随时关闭。纯 Python 标准库，无第三方运行依赖。

> 独立社区项目，与 OpenAI 无隶属或背书关系。目前没有 token、费用或回答质量的对照实验，不承诺节省比例。仅发布本项目的包装程序，不包含桌面应用、原版 CLI、账户或模型访问权限。

## 工作方式

在当前桌面应用的 CLI 启动入口与原版 App Server 之间加入本地 JSONL 包装程序。在 `turn/start` 发送到原版服务端前选档；仍使用同一聊天、登录状态和原版 CLI，不需要 API key。

## 状态与边界

本程序使用本地启发式规则，不是能够完整理解任务的 AI 分类器。普通任务默认 medium，明确的简单工作 low，排查与跨模块工作 high，疑难架构与数据完整性 xhigh，形式化证明或开放性难题 max；不自动使用 ultra。文件数量、文本长度、耗时不作为升级依据。图片和资料引用只做保守判断，不读取图片内容。

“继续”沿用本进程记录或服务端恢复的上一档位。独立的新需求重新判断。重启恢复时只有上次档位，没有自动获得整个对话语义。规则有可能高估或低估任务，应用内手动改档或 `pin` 可覆盖。

新安装默认对 `model/list` 已公布且支持已知档位的模型生效，不限定模型名称，也不会切换模型。自动档位取“不超过规则目标和 ceiling 的最高可用档位”；例如规则要求 xhigh、模型最高支持 high，就使用 high。若没有合适档位，保持原请求。用户明确指定或固定的档位不会被替换成其他档位。

工具结果、正在运行的轮次内追加消息、队列启动、未知模型目录、未知协议结构保持原请求。并非在一次生成的内部实时变档。权限、工具、消息内容、审批字段和响应均不修改。

计划模式的 `collaborationMode.settings.reasoning_effort` 优先于顶层 effort，因此两处同步填写，保留其余设置。手动修改会话档位成功后会保存该会话的固定档位，避免与用户争抢。重复同步相同档位不会固定。

## 安装

需要 macOS、Python 3.9+ 和已安装且已登录的兼容桌面应用。Windows 不支持此实现（依赖 Unix 管道和文件锁）；Linux 可运行协议包装和离线测试，但本项目没有 Linux 桌面启动安装器。


```sh
git clone https://github.com/mixx993/codex-auto-effort.git
cd codex-auto-effort
python3 install.py install
```

安装器查找系统或用户 Applications 目录中的 `ChatGPT.app` / `Codex.app`。其他位置可指定：

```sh
python3 install.py install --cli "/path/to/Codex.app/Contents/Resources/codex"
```

这里只是路径查找支持，不代表所有同名应用版本都兼容。安装后启动脚本使用此次安装的 Python 解释器；请保留该解释器，避免使用临时虚拟环境。安装器会保存原启动环境，遇到已有其他自定义 CLI 入口时停止。普通安装错误会回滚文件、登录服务和启动环境；若回滚也失败，会明确报告。断电或强制杀进程不在回滚保证范围内。

安装位置 `~/.codex/effort-controller/`。安装器设置当前用户 LaunchServices 启动环境中的 `CODEX_CLI_PATH` 与 `CODEX_APP_SERVER_FORCE_CLI`，并用 `~/Library/LaunchAgents/local.codex.effort-environment.plist` 在登录时恢复。原应用包和全局 `config.toml` 不改动。

这两个启动入口来自 2026-09-13 本机 `/Applications/ChatGPT.app/Contents/Resources/app.asar` 的只读检查，属于当前版本实现细节，不保证未来版本兼容。原版 CLI 为 `0.154.0-alpha.6.2`。这些入口不是本项目可保证稳定的公共扩展接口。

**首次安装后需等现有任务结束，再正常退出并重新打开桌面应用。安装器不强制退出应用。** 原本已经启动的服务端不会被替换。若新进程未出现 `proxy_started` 日志，不能认为桌面已接入。

## 使用与检查

### 菜单栏显示实际档位（可选）

底部模型选择器可能一直显示手动选择的档位；它不一定同步后台改写后的请求。可安装独立的只读菜单栏应用，显示 `M ✓` 等紧凑状态（L / M / H / XH / MAX / UL）：

```sh
python3 build_menubar.py
open "$HOME/Applications/Codex Auto Effort.app"
```

需要 macOS 13+ 和 Xcode Command Line Tools 中的 Swift 编译器。只在本机构建并进行 ad-hoc 签名，没有分发已公证二进制。生成的应用使用构建时的 Python，需保留该解释器。

- `✓`：读取到服务端上下文或本次请求之后的服务端设置，点开查看来源和时间。
- `…`：仅已选择或请求已接受，实际档位还没有确认。
- `!`：请求被拒绝。
- `OFF`：自动选档已关闭；历史记录不会被当成新的执行状态。
- `?`：监测尚未连接或已停止，不表示当前模型档位。

点开菜单突出显示档位、确认状态和一行任务名称；“详情”收纳来源、时间、原档位与原因，“查看任务”用于切换任务。默认跟随最近自动路由的任务，也可以固定查看一个任务；**不会自动识别当前前台聊天，也不表示该任务仍在生成**。时间显示的是记录时间。新请求到来时不会把上一轮上下文作为确认。

任务名称优先使用本地 `session_index.jsonl` 中的正式名称，随索引中的重命名记录更新；缺失时回退数据库标题，附件描述不会用作任务名。

菜单栏只读本地审计日志、任务名称索引、任务标题/日志路径和会话中的 `turn_context` 元数据，不增加模型请求，也不保存或输出提示词。旧版日志按任务和时间核对；新版有 turn ID 时会标明对应关系。日志/数据库格式变化、缺少文件或读取窗口内没有上下文时，会保持待确认。

它兼容已安装的旧包装程序，不需要为显示菜单栏重启 Codex。新版本包装日志会额外记录请求原档位与 turn ID，旧日志显示“未记录”。应用没有自动设置登录启动；退出菜单栏不会关闭后台自动选档。删除生成的 `.app` 即可移除此显示工具。构建器不会覆盖已有应用，更新时可用 `--output` 指定新路径。

无界面检查：`python3 monitor.py`；连续输出：`python3 monitor.py --watch`。

### 管理命令

```sh
~/.codex/effort-controller/codex-effort status
~/.codex/effort-controller/codex-effort preview '排查跨模块的未知错误'
~/.codex/effort-controller/codex-effort off
~/.codex/effort-controller/codex-effort on
~/.codex/effort-controller/codex-effort pin 会话ID high
~/.codex/effort-controller/codex-effort auto 会话ID
~/.codex/effort-controller/codex-effort auto all
~/.codex/effort-controller/codex-effort ceiling xhigh
~/.codex/effort-controller/codex-effort model '*'  # 所有目录中兼容模型
~/.codex/effort-controller/codex-effort model MODEL_ID  # 仅这个模型
```

模型筛选、开关和固定档位在下一次新一轮消息读取，无需再次重启。关闭后当前聊天保留最后已设置的档位；不会偷偷恢复另一个档位。直接在提示词单独一行写“推理强度设为high”也可以指定本轮档位。

从旧版本升级时保留已有的精确模型筛选、开关和固定档位。若希望开启通用模型支持，执行上面的 `model '*'`。安装目录、`codex-effort` 命令及 LaunchAgent 名称为兼容旧安装而保留；仓库更名不迁移运行目录。

`audit.jsonl` 只记录会话 ID、档位、原因、时间和状态，不记录提示词、附件、工具参数或凭据。日志轮转上限约两份各 2 MB。

- `proxy_started`：包装进程启动，不能证明某轮选档成功。
- `selected`：已改写即将发送的请求，不能证明服务端应用了设置。
- `accepted`：服务端接受该请求，仍不等同于完成或质量保证。
- `server_settings`：服务端发送的实际会话设置；用于核对档位。
- `rejected` / `skipped` / `passthrough_error`：失败或保持原请求。程序不会自动重试用户任务。

## 关闭启动接入

```sh
cd codex-auto-effort  # 进入你克隆的仓库
python3 install.py uninstall
```

恢复安装前的启动环境，卸载登录配置，保留代码与日志。重新安装会保留原配置，包括关闭状态；需要时使用 `codex-effort on` 再启用。运行中的包装程序读取到关闭设置后直接透传；应用下次启动使用原入口。

## 验证

```sh
python3 -m unittest discover -v
```

模拟协议、真实原版 App Server 验证、桌面重启后的实测是三个独立证据层级。公开的验证摘要见 [docs/verification.md](docs/verification.md)。本地探针生成的 `verification.json` 包含版本、模型目录及测试结果，默认不提交；新版探针不导出原始审计日志。


只检查本机服务端连接与模型目录（不发起模型推理）：

```sh
python3 live_probe.py
```

可选真实推理测试会发起两轮请求并使用账号额度：

```sh
python3 live_probe.py --live --model MODEL_ID
# 也可添加 --cli 指定原版 CLI
```

真实推理必须显式指定 `--model`；探针不会替你选择模型。目录检查会读取所有分页，未找到目标模型会直接报告错误。

`preview` 只显示文本规则的分类，不读取会话固定档位、模型目录、图片或实际请求；不能作为桌面接入证据。

## 隐私与反馈

包装程序在内存中转发请求，不增加外部服务。原版 Codex 自身的网络通信继续由原应用负责。附件、代码块和常见上下文标签采用有限的文本过滤；这不是能可靠识别所有文档边界的语义隔离器。

日志中仍有会话 ID 和时间等元数据。提交 Issue 前请删除这些字段及个人路径，不要上传 `audit*.jsonl`、`verification.json`、`installation.json`、凭据或完整对话。仓库不包含个人截图和实际运行日志。

欢迎提供脱敏的规则误判样例、协议兼容性报告或聚焦修复。提交代码前运行上述离线测试，注明操作系统、Python 和原版 CLI 版本；不将模拟协议通过写成真实桌面验证。参见 [CONTRIBUTING.md](CONTRIBUTING.md)。


本轮检查发现的问题、已完成优化及剩余限制见 [兼容性审查](docs/compatibility.md)。
