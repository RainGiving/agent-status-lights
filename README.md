# Claude Halo75 Status Lights

把 Claude Code 的任务状态映射到 **NuPhy Halo75 V2** 的背光。

灵感来自 [codex-kick75-status-lights](https://github.com/Pixelmoss/codex-kick75-status-lights)，
但硬件层是完全重写的：那个项目走 NuPhy Kick75 的私有 HID 协议，Halo75 V2 是标准 QMK + VIA，
两者没有任何共通之处。

## 开箱是什么样

**装完之后，默认只有中间的键区背光会动，四边的 Halo 环形灯不动。**

这不是功能没做完，是故意的：环形灯是整个项目里唯一一件原厂键盘做不到的事 —— 它在 VIA
上根本不存在（下面「为什么外圈是这样」有实测），必须刷入本项目的固件补丁才够得着。
如果默认就打开它，一台没刷过固件的键盘装完只会看起来是坏的。键区背光在原厂固件上就能用，
所以那才是默认打开的那个。

远程会话也一样：默认只跟踪**本机**的 Claude Code，不去轮询 Orca。

| 状态 | 内圈 · 键区背光（默认开） | 外圈 · Halo 环（默认关） | 触发 |
| --- | --- | --- | --- |
| 执行中 | 色带，跟随外圈色，60% | **彗星绕圈** 青 `#00A8FF` 拖尾 12 | `UserPromptSubmit` / `PostToolUse` |
| 等待权限 | 色带，跟随外圈色，75% | **整环同步脉冲** 琥珀 `#FFB000` | `PermissionRequest` |
| 工具失败 | 纯色，跟随外圈色，75% | **快闪** 红 `#FF2020`，保持 4 秒 | `PostToolUseFailure` / `StopFailure` |
| 全部完成 | 纯色，跟随外圈色，60% | **扫一圈填满** 绿 `#00E060`，保持 10 秒 | `Stop` |
| 默认 / 空闲 | 恢复接管前的键区灯效 | 交还固件 | 保持超时 / `SessionEnd` |

两圈都打开时它们**同时驱动、一起变色**。内圈默认「跟随外圈颜色」，你只需要为它单独选灯效
—— 这样两圈读起来是**一个信号**而不是两盏无关的灯。

**「默认 / 空闲」是一个可配置的状态**，不是写死的行为。默认是把两圈都交还固件
（也就是恢复你自己用 `Fn` 键设的灯效），但你也可以在这里指定一套固定的默认灯效。

**状态由运动区分，颜色只是强化。** 这不是为了好看：余光扫过时，"整环一起明灭"和
"一个点在转圈"的区别一眼就能分辨，而两种红色的区别不能。所以外圈打开之后，它才是主信号。

## 主动重连：打开外圈和远程会话

上面两件默认关掉的事，用一条命令一起打开：

```bash
/usr/bin/python3 scripts/install.py reconnect
```

或者在设置 App 的「高级」页点 **「重新连接（开启外圈 + Orca）」**。它做三件事：

1. **探测外圈固件。** 往 VIA 厂商通道 `0x10` 发一次 `halo-get`。原厂固件对这个通道回
   `0xff`（未处理），所以答得上来就等于补丁固件在机器上。答得上来才打开 `zones.halo`；
   答不上来它**不会**硬开一个不存在的功能，而是打印刷机步骤（见下面「升级外圈固件」）。
2. **打开 Orca 桥接**（`orca.json` 的 `enabled`），于是 Orca SSH 主机和配对的远程 runtime
   上的会话也开始喂进同一盏灯。
3. 通知后台服务立刻重读配置，不用重启。

退回默认：

```bash
/usr/bin/python3 scripts/install.py reconnect --off
```

外圈交还固件（你自己用 `Fn + M` 设的灯效原样回来），Orca 轮询停掉，只剩本机 + 键区背光。

### 升级外圈固件

`reconnect` 探测不到外圈时，先排除这三件事，它们比刷固件常见得多：

- 键盘不是 **USB 数据线**直连 —— 2.4G 接收器和蓝牙都不暴露 VIA 接口；
- VIA 或 NuPhy 官方软件开着 —— 它们会独占 raw HID；
- 固件确实还是原厂的。

确认是最后一种，才需要刷。**进 DFU 会清空 EEPROM，所以先备份改键**：

```bash
build/via_backup > backups/keymap.txt
# 按住 Esc 再插 USB
dfu-util -a 0 -d 0483:df11 -s 0x08000000:leave \
         -D ~/qmk_nuphy/nuphy_halo75_v2_ansi_via.bin
build/via_restore backups/keymap.txt
```

完整步骤、工具链的坑、以及怎么用 `dfu-util -U` 先 dump 一份原厂固件当回滚镜像，
都在 [`firmware/README.md`](firmware/README.md)。**补丁只实现动画原语**，颜色、速度、
拖尾、亮度全部运行时经 VIA 下发 —— 所以调参不需要重刷，这一点很重要，因为每次重刷都要
走一遍会清 EEPROM 的 DFU 循环。

### 为什么外圈是这样

对 `id_custom_get_value` 扫遍全部 256 个通道，只有 `0x03`（标准 QMK RGB Matrix）会回话；
从键盘上用 `Fn + M` 改环形灯的颜色和效果，VIA 可读状态里**没有任何一个字节**跟着动。
环形灯由一个 VIA 完全不暴露的固件子系统驱动。

这件事的好一面是：本项目对键区背光的写入**碰不到环形灯**。唯一的例外是 effect `0`
（`RGB_MATRIX_NONE`），它会关掉整块 LED 驱动、把环形灯一起带走，所以代码里到处都拒绝
effect 0。测量细节见 [`docs/PROTOCOL.md`](docs/PROTOCOL.md)。

## 多个会话

按 `session_id` 分别跟踪，取**优先级最高**的显示：

```text
失败 > 等待权限 > 执行中 > 全部完成 > 默认
```

所以只要有**任何一个**会话要你批准就会看到琥珀脉冲；而绿色只在**所有**会话都结束时出现
（它优先级最低，任何一个还在跑都会压住它）。App 的「设备」页会列出每个状态各有几个会话。

被强杀的 Claude Code 不会发出 `SessionEnd`，那个会话会一直卡在「执行中」。所以活跃状态
另有一个更短的超时（默认 30 分钟无事件即丢弃），否则灯会一直转下去。

## 远程会话（Orca 桥接）

**默认关闭**，`reconnect` 打开。

hook 只在 **`claude` 进程所在的那台机器上**触发。所以在 Orca 的 SSH 主机、或配对的远程
Orca runtime 上起的会话，事件根本到不了本机 —— 那边既没有 `halo75_hook`，也连不上本机的
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

配置在 `~/Library/Application Support/ClaudeHalo75/orca.json`，**独立于 `settings.json`**：
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

## 其他 Agent：Codex

**Codex CLI 可以直接接**，而且 `halo75_hook` 一个字节都不用改。

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

## 要求

- macOS
- NuPhy Halo75 V2，**USB 数据线连接**（2.4G 接收器和蓝牙都不暴露 VIA 接口）
- Xcode Command Line Tools（`clang`）
- Python 3.9+（macOS 自带的 `/usr/bin/python3` 即可）
- Claude Code 2.1.x（需要 `PermissionRequest` 和 `PostToolUseFailure` 事件）
- 外圈（`zones.halo`）需要刷入 [`firmware/halo-host-control.patch`](firmware/README.md)；
  内圈在原厂固件上即可工作

## 安装

先单独验证硬件，这一步不改任何配置：

```bash
/usr/bin/python3 scripts/install.py test-hid
```

预期：打字区变绿约 5 秒后恢复原样，Halo 灯带全程不动。

确认没问题后安装：

```bash
/usr/bin/python3 scripts/install.py install
```

安装器会：

1. 编译 `halo75_ledctl`（HID 控制器）和 `halo75_hook`（hook 客户端）。
2. 把运行文件放到 `~/Library/Application Support/ClaudeHalo75/`。
3. 把 7 个 hook **合并**进 `~/.claude/settings.json`
   （**先做带时间戳的备份**，只追加自己的条目，不动其他工具的 hook）。
4. 安装并启动用户级 LaunchAgent。

然后**完全退出**并重新打开 Claude Code（关窗口不会重载 hook）。

```bash
/usr/bin/python3 scripts/install.py status
```

### 设置 App

```bash
/usr/bin/python3 scripts/install.py install-app
open "$HOME/Applications/Claude Halo75.app"
```

一个独立窗口，左侧导航分「设备 / 状态灯 / 系统」三组：

- **设备** — 服务、固件、hooks 三项状态，以及当前状态和活跃会话数
- **执行中 / 等待权限 / 工具失败 / 全部完成** — 每个状态一页：选动画、取色、
  调亮度 / 速度 / 拖尾，**左边有一个 45 点的环形实时预览**，按固件同样的算法动，
  调参不用一直盯着键盘；旁边的按钮可以在真键盘上预览 3 秒
- **Hooks** — 7 个事件的安装状态，一键装 / 卸
- **高级** — 重新连接、驱动哪些区域、保持时间、配置文件位置

菜单栏还有一个图标，可以快速打开窗口、交还灯光或退出。

预览要求后台服务在跑且键盘 USB 连着；保存不要求，服务下次启动会读到。
App 只是设置界面，**不替代后台服务**。重启 Mac 后需要重新打开（暂未注册为登录项）。

#### 换图标

把一张**正方形、1024×1024** 的 PNG 放成 `assets/icon.png`，然后重新构建：

```bash
/usr/bin/python3 scripts/install.py install-app
```

构建时会用 `sips` + `iconutil` 生成 10 个尺寸打包成 `AppIcon.icns` 塞进 bundle。
十个尺寸都要生成，不能只放一张大的：Dock、Cmd-Tab、访达列表、显示简介各读不同的尺寸，
bundle 里缺了正在用的那个，macOS 会退回通用图标而不是去缩放相邻的。

没有 `assets/icon.png` 时构建照常成功，App 用通用图标。单独重新生成：
`install.py icon`。换完图标 Dock 可能还显示旧的（图标缓存），`killall Dock` 即可。

## 配置

`~/Library/Application Support/ClaudeHalo75/settings.json`，改完立即生效，不用重启服务
（配置按 mtime 缓存）。**动画参数全部走线传给固件，所以调参不需要重刷固件。**

```json
{
  "version": 3,
  "zones": { "halo": false, "matrix": true },
  "completed_hold_seconds": 10,
  "failure_hold_seconds": 4,
  "stale_active_minutes": 30,
  "stale_session_hours": 12,
  "states": {
    "running": {
      "halo":   { "color": "#00A8FF", "brightness": 100, "mode": "comet", "speed": 200, "param": 12 },
      "matrix": { "color": "#00A8FF", "brightness": 60, "effect": 5, "speed": 110,
                  "follow_color": true, "restore": false }
    }
  }
}
```

`zones` 决定驱动哪些区域。**关掉的区域完全不碰**：不读、不写，也不会去恢复它。
`halo` 默认 `false`（见开头），`reconnect` 会把它打开。

每个状态各有 `halo` 和 `matrix` 两份配置，五个状态是
`running` / `permission` / `failure` / `completed` / `idle`。

**外圈 `halo`：**

| 字段 | 说明 |
| --- | --- |
| `mode` | `solid` / `pulse` / `comet` / `strobe` / `fill`。`release`（交还固件）只有 `idle` 能用，用在活跃状态上会在正要报信时把环灯灭掉，所以被拒绝 |
| `color` | `#RRGGBB`，环形灯直接吃 RGB，不经过 HSV 转换 |
| `brightness` | 0–100 |
| `speed` | 0–255，越大越快 |
| `param` | `comet` 的拖尾长度（颗），或 `strobe` 的占空比（%）；其余模式忽略 |

**内圈 `matrix`：**

| 字段 | 说明 |
| --- | --- |
| `effect` | 1–42，QMK RGB Matrix 效果号，见 [`docs/PROTOCOL.md`](docs/PROTOCOL.md)。`0` 会关掉整块 LED 驱动并带走环形灯，永远被拒绝 |
| `color` | `#RRGGBB`，转成 QMK 的 HSV 下发 |
| `follow_color` | `true` 时忽略上面的 `color`，改用同一状态里外圈的颜色。默认开，这是两圈读起来像一个信号的原因 |
| `restore` | `true` 时不画任何东西，而是把接管前存下来的键区灯效写回去。只在 `idle` 上有意义 |
| `brightness` | 0–100 |
| `speed` | 0–255 |

非法值会被忽略并写进日志，不会覆盖已有的有效配置。

手动试某个效果，不必等真实任务：

```bash
LEDCTL=~/Library/Application\ Support/ClaudeHalo75/halo75_ledctl
"$LEDCTL" halo comet 0 168 255 200 12 255   # mode r g b speed param brightness
"$LEDCTL" halo-get                          # 从固件读回当前动画
"$LEDCTL" halo release 0 0 0 0 0 0          # 交还固件
"$LEDCTL" get                               # 读回键区背光
```

## 为什么 `failure` 只保持 4 秒

`Bash` 退出码为 1 就会触发 `PostToolUseFailure` —— `grep` 没匹配到这种日常操作也算。
在本机实测数据里它占 `PostToolUse` 的约 2.8%。所以红色被设计成一次**短暂闪烁**而不是一种模式：
保持 4 秒后，下一次工具调用就会把它恢复成黄色。想让红色一直留到本轮结束，把
`failure_hold_seconds` 调大即可。

## 管理命令

```bash
/usr/bin/python3 scripts/install.py build                  # 只编译 C 部分
/usr/bin/python3 scripts/install.py build-app              # 只构建 .app
/usr/bin/python3 scripts/install.py icon                   # 只从 assets/icon.png 生成 .icns
/usr/bin/python3 scripts/install.py install                # 安装或升级
/usr/bin/python3 scripts/install.py install-app            # 构建并安装设置 App
/usr/bin/python3 scripts/install.py status                 # 服务 / hooks / 外圈 / codex / orca / 键盘
/usr/bin/python3 scripts/install.py reconnect              # 开启外圈 + Orca 桥接
/usr/bin/python3 scripts/install.py reconnect --off        # 退回默认（只有内圈、只有本机）
/usr/bin/python3 scripts/install.py hooks-install          # 只装 Claude Code hooks（App 的按钮走这条）
/usr/bin/python3 scripts/install.py hooks-uninstall        # 只卸 Claude Code hooks
/usr/bin/python3 scripts/install.py codex-hooks-install    # 装 Codex CLI hooks
/usr/bin/python3 scripts/install.py codex-hooks-uninstall  # 卸 Codex CLI hooks
/usr/bin/python3 scripts/install.py test-hid               # 可恢复的 5 秒硬件测试
/usr/bin/python3 scripts/install.py send-event Stop        # 不用真跑任务就能测状态机
/usr/bin/python3 scripts/install.py uninstall              # 全部移除并恢复灯效
```

`install` 会在安装时按 Claude Code 的方式（`/bin/sh -c`）实际执行一次 hook 并检查退出码
和 stdout。这一步是必须的：运行时目录路径含空格（`Application Support`），命令不加 shell
引用就会被 `sh` 在空格处截断，每次 hook 都静默地以 127 退出。

日志：

```bash
tail -f ~/Library/Application\ Support/ClaudeHalo75/daemon.log
```

## 隐私

hook 客户端把 hook 的原始 JSON 转发给本机 Unix socket，守护进程**只**保留
`hook_event_name` 和 `session_id`，其余全部丢弃。提示词、工具参数、工具输出都不会
写进状态文件或日志。socket 和配置文件权限均为 `0600`。

打开 Orca 桥接后，轮询会读到远程终端的标题和预览文本（用来判断状态）。这些内容同样
**不落盘** —— 只用来算出一个状态名，用完即弃。这也是它默认关着的原因之一。

## 故障排查

**灯没反应** — 确认是 USB 数据线而不是 2.4G/蓝牙；退出 VIA / NuPhy 官方软件（它们会独占 raw HID）；
跑 `status` 看 `keyboard` 那一行。

**只有键区亮，四边不亮** — 这是默认行为。跑 `reconnect`，它会告诉你是固件的问题还是连接的问题。

**`hooks: 0/7`** — 重跑 `install`，然后用 `Command + Q` 完全退出 Claude Code 再打开。

**灯卡在某个颜色** — Claude Code 异常退出、没发 `Stop`。跑
`send-event SessionEnd`，或直接重启服务。守护进程启动时也会自动恢复。

**Halo 灯带灭了，而且 `zones.halo` 是关的** — 本项目碰不到它。用 `Fn + M + ↑` 调亮度，
或 `Fn + M + ?` 切换区域开关。

**换了图标但 Dock 还是旧的** — 图标缓存。`killall Dock`。

## 已知限制

- 只支持 macOS 和 Halo75 V2（`19f5:32f5`）。
- 外圈需要自编译固件；官方固件更新会覆盖掉，得重刷。
- 45 颗环形灯作为一个整体显示状态。固件里那 5 个 `Fn+M+?` 分区没有单独暴露出来。
- 远程会话只能拿到「执行中 / 等待权限 / 完成」三档，**工具失败测不出来**；而且只覆盖在
  Orca 里起的会话 —— 你自己开终端 `ssh` 过去敲的 `claude`，Orca 看不见，灯也不会动。
- 远程状态依赖 Orca 未文档化的终端标题字形，Orca 改了词表就会读错（不会崩，只是读错）。
  另外 spinner 字形是通用的，远程跑 codex 之类的 agent 也会被算成「执行中」。
- Codex 那条路没有红色（它没有失败事件），而且尚未在真机验证过。
- 键区背光亮度 255 不可达，上限 254（固件 off-by-one，肉眼无差别）。
- NuPhy 固件更新可能改变以上任何一条。

## License

MIT。非官方工具，与 Anthropic、OpenAI、NuPhy 均无隶属关系。
