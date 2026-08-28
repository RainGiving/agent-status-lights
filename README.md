# Agent Status Lights

把编码 agent 的状态映射到 QMK 键盘的背光上。**执行中 / 等你批准 / 出错 / 完成** ——
四种状态用**运动**区分，颜色只是强化：余光扫过时"一个点在转圈"和"整环一起明灭"能分辨，
两种红色不能。

![执行中：彗星绕着底座转圈，键区跟着同一个颜色](docs/demo.gif)

支持 **Claude Code** 和 **Codex CLI**，本机会话走 hook，Orca 里的远程会话走轮询。
键盘靠 VIA 的 raw HID 接口发现，不认写死的 VID/PID。

---

## 装

把这行粘给 Claude Code 或 Codex：

```text
帮我装 https://github.com/royzjq/agent-status-lights ：克隆到 ~/Desktop/projects，
然后严格按照仓库里的 docs/AI-INSTALL.md 执行，遇到需要我动手的步骤停下来问我。
```

它会照 [`docs/AI-INSTALL.md`](docs/AI-INSTALL.md) 走完扫描、安装、验收，遇到要你动手的
地方（只有刷固件那一步）会停下来问。

自己装也行：

```bash
/usr/bin/python3 scripts/install.py scan      # 只读，看接了什么键盘、它有哪些灯
/usr/bin/python3 scripts/install.py install   # 装服务和 hooks
/usr/bin/python3 scripts/install.py install-app
```

装完**用 Command + Q 完全退出 Claude Code 再打开** —— hook 是启动时读的，关窗口不算。

需要：macOS、USB 数据线直连的 QMK/VIA 键盘、Xcode CLT、系统自带的 `/usr/bin/python3`。

## 长什么样

默认**只有中间的键区背光会动**，四边的 Halo 环形灯不动。

| 状态 | 内圈 · 键区背光（默认开） | 外圈 · Halo 环（默认关） |
| --- | --- | --- |
| 执行中 | 色带，跟随外圈色 | **彗星绕圈** 青 `#00A8FF` |
| 等待权限 | 色带，跟随外圈色 | **整环脉冲** 琥珀 `#FFB000` |
| 工具失败 | 纯色，跟随外圈色 | **快闪** 红 `#FF2020`，4 秒 |
| 全部完成 | 纯色，跟随外圈色 | **扫圈填满** 绿 `#00E060`，10 秒 |
| 默认 / 空闲 | 恢复你原本的灯效 | 交还固件 |

外圈默认关着，因为它是唯一一件原厂键盘做不到的事 —— 它在 VIA 上根本不存在，必须刷本项目的
固件补丁。默认打开的话，没刷过固件的键盘装完只会看起来是坏的。

多个会话同时跑时取优先级最高的：**失败 > 等待权限 > 执行中 > 完成 > 默认**。
细节在 [AGENTS](docs/AGENTS.md)。

## 打开外圈和远程会话

```bash
/usr/bin/python3 scripts/install.py reconnect        # 开
/usr/bin/python3 scripts/install.py reconnect --off  # 退回默认
```

设置 App 的「高级」页也有按钮。它会先探测外圈固件在不在，**不在就打印刷机步骤而不是留一个
死开关**，然后打开 Orca 桥接（远程会话）。

外圈需要自编译固件，步骤和工具链的坑在 [`firmware/README.md`](firmware/README.md)。
**进 DFU 会清空 EEPROM，先备份改键**（`build/via_backup`）。

## 设置 App

一个独立窗口：每个状态一页，选动画、取色、调亮度速度拖尾，左边有 45 点环形实时预览，
按固件同样的算法动。还有设备扫描结果、hooks 安装状态、重新连接按钮。

配置在 `~/Library/Application Support/ClaudeHalo75/settings.json`，改完立即生效。
**动画参数全部走线下发，调参不用重刷固件。**

| 字段 | 说明 |
| --- | --- |
| `zones` | `{"halo": bool, "matrix": bool}`，驱动哪些区域。关掉的完全不碰 |
| `halo.mode` | `solid` / `pulse` / `comet` / `strobe` / `fill` |
| `halo.param` | `comet` 的拖尾长度，或 `strobe` 的占空比（%） |
| `matrix.effect` | 1–42，QMK RGB Matrix 效果号，见 [PROTOCOL](docs/PROTOCOL.md) |
| `matrix.follow_color` | 用同状态外圈的颜色。默认开，这是两圈读起来像一个信号的原因 |
| `matrix.restore` | 不画东西，把接管前的灯效写回去。只对 `idle` 有意义 |

## 命令

```bash
scan [--deep] [--json]   # 只读：接了什么键盘、有哪些灯光通道
install / uninstall      # 装或全部移除（恢复灯效，只摘自己的 hook）
install-app / icon       # 设置 App / 从 assets/icon.png 生成图标
reconnect [--off]        # 外圈 + Orca 开关
status                   # 服务 / hooks / 外圈 / codex / orca / 键盘
codex-hooks-install      # 接上 Codex CLI
send-event Stop          # 不跑真任务测状态机
```

日志：`tail -f ~/Library/Application\ Support/ClaudeHalo75/daemon.log`

## 更多

- [**AI-INSTALL**](docs/AI-INSTALL.md) — 给 agent 看的安装 runbook
- [**AGENTS**](docs/AGENTS.md) — Codex 的边界、Orca 桥接、多会话怎么合并
- [**PROTOCOL**](docs/PROTOCOL.md) — VIA 实测记录：通道扫描、亮度 off-by-one、并发读为什么会串台
- [**firmware**](firmware/README.md) — 外圈固件补丁、构建和刷机

## 隐私

hook 客户端把原始 JSON 转发给本机 Unix socket，守护进程**只**保留 `hook_event_name` 和
`session_id`，其余全部丢弃。提示词、工具参数、工具输出都不会落盘。socket 和配置权限 `0600`。

Orca 桥接（默认关）会读远程终端的标题和预览来判断状态，这些内容同样不落盘。

## 排查

| 症状 | 先做什么 |
| --- | --- |
| 灯完全没反应 | 跑 `scan`。多半是接了 2.4G/蓝牙（不暴露 VIA），或 VIA / NuPhy 官方软件占着 raw HID |
| 只有键区亮，四边不亮 | 这是默认行为。跑 `reconnect` |
| `hooks: 0/7` | 重跑 `install`，然后 `Command + Q` 完全退出 Claude Code |
| 灯卡在某个颜色 | agent 异常退出没发 `Stop`。`send-event SessionEnd` |
| 换了图标 Dock 还是旧的 | `killall Dock` |

## 已知限制

- 只支持 macOS。扫描对任何 QMK/VIA 键盘有效，内圈原理上也通用，但**只在 NuPhy Halo75 V2
  （`19f5:32f5`）上真机验证过**。
- 外圈需要自编译固件；官方固件更新会覆盖掉，得重刷。45 颗灯作为一个整体显示状态。
- Codex 没有失败事件，**Codex 会话不会变红**；这条路径也还没在真机验证过。
- 远程会话只有「执行中 / 等待权限 / 完成」三档，测不出工具失败，且只覆盖 Orca 里起的会话。
- 键区背光亮度上限 254（固件 off-by-one，肉眼无差别）。

灵感来自 [codex-kick75-status-lights](https://github.com/Pixelmoss/codex-kick75-status-lights)，
硬件层是完全重写的：那个项目走 Kick75 的私有 HID 协议，Halo75 V2 是标准 QMK + VIA。

MIT。非官方工具，与 Anthropic、OpenAI、NuPhy 均无隶属关系。
