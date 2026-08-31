# 接哪些 agent，以及多个会话怎么合并

README 只说"支持 Claude Code 和 Codex，远程会话走 Orca"。这里是细节和它们各自的边界。

## 多个会话

按 `session_id` 分别跟踪，取**优先级最高**的显示：

```text
失败 > 等待权限 > 执行中 > 全部完成 > 默认
```

所以只要有**任何一个**会话要你批准就会看到琥珀脉冲；而绿色只在**所有**会话都结束时出现
（它优先级最低，任何一个还在跑都会压住它）。App 的「设备」页会列出每个状态各有几个会话。

被强杀的 Claude Code 不会发出 `SessionEnd`，那个会话会一直卡在「执行中」。所以活跃状态
另有一个更短的超时（默认 30 分钟无事件即丢弃），否则灯会一直转下去。

## 为什么 `failure` 只保持 4 秒

`Bash` 退出码为 1 就会触发 `PostToolUseFailure` —— `grep` 没匹配到这种日常操作也算。
在本机实测数据里它占 `PostToolUse` 的约 2.8%。所以红色被设计成一次**短暂闪烁**而不是一种模式：
保持 4 秒后，下一次工具调用就会把它恢复成黄色。想让红色一直留到本轮结束，把
`failure_hold_seconds` 调大即可。

## 其他 Agent：Codex

**Codex CLI 可以直接接**，而且 `halo65_hook` 一个字节都不用改。

Codex 的 hook 契约跟 Claude Code 是同一套：同样的事件名，stdin 上同样的
`session_id` 和 `hook_event_name` —— 而这两个字段正好就是本项目唯一会读的两个（其余全丢，
见「隐私」）。区别只有配置文件的位置：Codex 读 `~/.codex/hooks.json` 或 `config.toml` 里的
`[[hooks.*]]`，不是 `~/.claude/settings.json`。

```bash
/usr/bin/python3 scripts/install.py codex-hooks-install
/usr/bin/python3 scripts/install.py codex-hooks-uninstall
```

装 5 个事件，映射跟 Claude Code 完全一致：

| Codex 事件 | 状态 |
| --- | --- |
| `UserPromptSubmit` / `PostToolUse` | 执行中 |
| `PermissionRequest` | 等待权限 |
| `Stop` | 全部完成 |
| `SessionEnd` | 丢弃该会话 |

**少的是红色。** Codex 没有 `PostToolUseFailure`，也没有 `StopFailure` —— 这两个事件在它
的事件表里不存在，所以 Codex 会话永远不会把灯变红。失败信息其实在 `PostToolUse` 的
`tool_response` 里，但要读它就得让 hook 去解析 payload，而现在的设计是 hook 只做转发、
守护进程只留两个字段，这条隐私边界比一个红灯值钱，所以没做。

两边的会话共用同一个聚合器，按 `session_id` 区分（都是 UUID，不会撞），优先级规则照旧
—— 一个 Codex 在跑、一个 Claude 在等你批准，你看到的是琥珀色。

> ⚠️ 这条路径是**照 Codex 的 hooks 文档写的，没有在真机上跑过** —— 写这段时本机没装
> Codex。安装器会像 Claude Code 那条路一样先自测一次 hook 命令（用 `/bin/sh` 实跑、
> 检查退出码和 stdout），但事件是否真的按预期触发，需要你装上 Codex 之后用
> `install.py status` 的 `codex:` 那一行和 `daemon.log` 确认。

Orca 桥接那条路是 agent 无关的：它靠终端标题里的 spinner 字形判断，所以远程跑 Codex
或别的 agent 也会被算成「执行中」。这是 Orca 自己的检测就有的盲点，不是这里新增的。

## 远程会话（Orca 桥接）

**默认关闭**，`reconnect` 打开。

hook 只在 **`claude` 进程所在的那台机器上**触发。所以在 Orca 的 SSH 主机、或配对的远程
Orca runtime 上起的会话，事件根本到不了本机 —— 那边既没有 `halo65_hook`，也连不上本机的
unix socket。判断标准只有一条：**进程跑在哪**。你在本机跑 claude、让它自己 ssh 出去干活，
灯是正常的；你在远程机器上敲 claude，灯就是死的。

Orca 会在它管理的每台主机上装自己的 hook，所以这些会话它看得见。`src/orca_bridge.py`
就是去轮询 `orca` CLI 把它们读回来，作为伪会话（键名 `orca:<scope>:<handle>`）喂进同一个
优先级聚合器。本机会话仍然走 hook —— 轮询器**故意跳过**它们，免得用粗粒度状态覆盖掉细的。

它默认关着，除了「原厂能不能用」之外还有一个理由：轮询每隔两秒多起一批子进程，去读每台
配对 runtime 上终端的标题和预览内容。这比点亮一盏灯更值得你自己按一下同意。

保真度天然不如 hook，这是方案本身的代价：

| 状态 | 来源 | 可靠性 |
|---|---|---|
| 执行中 | 终端标题里的 spinner 字形 | 可靠 |
| 等待权限 | 扫终端预览末尾的提示词 | 尽力而为，见下 |
| 全部完成 | 检测到「执行中 → 空闲」跳变后保持一段时间 | 可靠 |
| 工具失败 | —— | **测不到，永不触发** |

状态词表取自 Orca 自己的 `detectAgentStatusFromTitle()`：`✳ ` 前缀是空闲，盲文
（U+2800–U+28FF）或四分圆（U+25D0–U+25D3）spinner 是执行中。Claude Code **不会**在标题里
放权限标记（Orca 那个 `✋` 是 Gemini CLI 的），所以「等待权限」只能靠扫 `preview` 末尾的
提示词 —— 措辞会随版本变，因此做成了配置项而不是写死的正则。

配置在 `~/Library/Application Support/ClaudeHalo65/orca.json`，**独立于 `settings.json`**：
设置 App 是用一个只认识已知字段的 Codable 结构体来回写 `settings.json` 的，加在那里的键会在
用户下次保存时被静默丢掉。文件不存在等于关闭，也就是全新安装的状态。

```jsonc
{
  "enabled": true,              // reconnect 打开的就是这一项
  "poll_seconds": 2.5,          // 每轮约 0.2s×(1+环境数) 的子进程开销
  "include_local_host": false,  // 打开会让本机会话也走轮询，仅调试用
  "environments": "auto",       // 自动发现配对的远程 runtime；也可写 id 列表，[] 表示只看本机 runtime（仍覆盖 SSH 主机）
  "detect_permission": true,
  "permission_markers": ["do you want to proceed?", "don't ask again"]
}
```

`python3 scripts/install.py status` 会打印桥接的最近一次轮询时间、远程会话数和错误。
Orca 没开或 CLI 不在时，桥接只是退避重试，本机那条路完全不受影响。

## 第三条输入：语音输入

前两条输入都在说后台在做什么。`halo65_voice` 说的是**你此刻正在做什么**，所以
`voice` 状态排在优先级最前面：正在口述的时候，盖住一个还在转圈的彗星是对的。

它是一个独立的启动项 `com.claudehalo65.voice`，不是守护进程拉起来的子进程。原因是
「输入监控」这项权限记在**发起请求的那个进程**上：由 Python 守护进程 fork 出来的话，
权限会挂到 `/usr/bin/python3` 头上，既授权给了整个 Python，也拿不到本项目自己的身份。

两条触发方式可以单开也可以都开：

| 方式 | 权限 | 结束条件 |
| --- | --- | --- |
| `hotkey` | 输入监控 | `hold` 松开按键，`toggle` 再按一次 |
| `microphone` | 不需要 | 默认输入设备停止录音 |

事件监听只做一件事：把每个按键事件的键码和四个修饰键与配置的那一组比对，命中就往
守护进程的 socket 上发 `{"source": "voice", "active": true/false}`。**不上报按了什么键**，
不命中的按键连一个字节都不会离开这个进程。麦克风那条是每 0.25 秒读一次
`kAudioDevicePropertyDeviceIsRunningSomewhere`，只读设备属性，不打开音频流，
所以不触发麦克风权限。

配置由守护进程从 `settings.json` 渲染成 `voice.conf`，监听进程按 mtime 重读，
所以在设置 App 里改快捷键不需要重启任何东西。权限没给到的时候它写一行
`voice.status` 然后退出，由 launchd 按 300 秒的节流重启 —— 授权是新进程才生效的，
留在原地重试没有意义。
