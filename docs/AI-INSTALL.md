# AI 安装 runbook

这份文件是写给 **agent** 看的，不是写给人看的。用户会把一段指令粘贴给 Claude Code 或
Codex，agent 读到这里，然后照着往下执行。

人用的安装说明在 [README](../README.md) 的「安装」一节。

---

## 给用户：粘贴这一段

```text
帮我安装 https://github.com/royzjq/agent-status-lights ：克隆仓库后，严格按照仓库里的
docs/AI-INSTALL.md 执行，遇到需要我动手的步骤停下来问我。如果我的键盘不是
NuPhy Halo65 V2，按文档里的适配分支告诉我哪些功能可用。
```

---

## 给 agent：先读这一段，它约束下面所有步骤

这套东西会**改用户的键盘固件**和**用户的 agent 配置文件**。以下几条不是建议：

1. **进 DFU 之前必须先备份 VIA 改键。** 进 bootloader 会清空 EEPROM
   （`bootmagic.c` 里的 `bootmagic_reset_eeprom()`），用户的改键会丢失。没备份就不许刷。
2. **不许扫 VIA 的命令号。** 通道号是 `0x08` 的参数，扫它安全；命令号不是 ——
   `0x0A` 是 `id_eeprom_reset`，`0x0B` 是 `id_bootloader_jump`。本项目的工具只发
   `0x01` / `0x02` / `0x07` / `0x08`，不要自己另写 HID 代码去试别的。
3. **进 DFU 这一步你做不了，必须停下来。** 它要求人按住 Esc 的同时插上 USB 线，
   没有任何命令能替代。走到那一步就停，把要做的事说明清楚，等用户回复「好了」再继续。
4. **不要手改 `~/.claude/settings.json` 或 `~/.codex/hooks.json`。**
   `install.py` 会做带时间戳的备份、只增删自己打了标记的条目。手改会碰到别人的 hook。
5. **不要跑 `install.py uninstall`**，除非用户明确要求。它会删除整个运行目录。
6. **扫描失败就停，不要猜。** 键盘没插、插的是 2.4G/蓝牙、或者 VIA 开着占用了接口 ——
   这三种情况的处理方式完全不同，猜错会让用户在错误的方向上排查很久。
7. **每一步的输出要复述给用户**，尤其是扫描结果。用户需要知道自己的键盘被认成了什么。

外圈固件那一整段（第 4 步）**只适用于 NuPhy Halo65 V2**，补丁是针对那块板子的
`side.c` 写的。蓝牙通道也来自同一份补丁。别的 QMK 键盘跳过第 4 步：内圈（键区背光）
在 USB 直连下可以直接用，外圈和蓝牙不可用，移植方向见 README 的「适配其他键盘」。

---

## 1. 部署

克隆到用户惯用的项目目录；用户没有说明时用 `~/agent-status-lights`。
下面的命令都通过 `REPO` 引用仓库位置：

```bash
REPO=~/agent-status-lights
git clone https://github.com/royzjq/agent-status-lights "$REPO"
cd "$REPO"
```

检查三个前置条件，缺哪个就告诉用户怎么装，不要静默跳过：

```bash
xcode-select -p          # Xcode Command Line Tools，缺了跑 xcode-select --install
swift --version          # 构建设置 App 用，跟着 CLT 一起装
/usr/bin/python3 -V      # macOS 自带，3.9+ 即可；不要换成 brew 的 python
```

然后编译 C 部分（`halo65_ledctl`、`halo65_hook`、`via_scan`、`halo65_voice`、
`halo65_leds`，以及刷固件时才用得上的 `via_backup` / `via_restore`）：

```bash
/usr/bin/python3 scripts/install.py build
```

这一步**不碰任何配置，也不碰键盘**。失败通常是 CLT 没装。

## 2. 扫描

```bash
/usr/bin/python3 scripts/install.py scan
```

只读，不写键盘任何一个字节。把完整输出复述给用户。需要机器可读的结果就用
`scan --json`。

## 3. 按扫描结果分支

**A. 一个设备都没找到**

停下来。按可能性从高到低问用户：

- 键盘是不是用 2.4G 接收器或蓝牙连的？**这两种都不暴露 VIA 接口**，安装必须插 USB 数据线
  （装好并刷过固件补丁之后，蓝牙才可用）。
- 线是不是只能充电的那种？换一根数据线。
- VIA 网页版 / NuPhy 官方软件 / 别的键盘配置软件是不是开着？它们会独占 raw HID，**退出它们**。

用户处理完重跑第 2 步。**不要**在这里往下走。

**B. 找到设备，但 `lighting` 是空的**

这块板子的固件没有通过 VIA 暴露任何灯光通道，本项目没有东西可以驱动。
如实告诉用户，到此为止。

**C. 找到设备，有 `rgb_matrix`，没有 `halo_ring`**

这是绝大多数情况，包括原厂固件的 Halo65 V2 和其他 QMK 键盘。
**内圈（键区背光）现在就能用，不需要刷任何固件。** 跳到第 5 步。

只有当**同时满足**下面两条时，才提第 4 步：

- 设备是 `0x19f5:0x3315`（NuPhy Halo65 V2），**并且**
- 用户明确说想要外圈那圈 50 颗 Halo 灯

提的时候要把代价说全：需要装 QMK 工具链（源码和子模块约 0.5 GB，ARM toolchain 约 0.9 GB）、
需要用户物理进 DFU、会清空 EEPROM。**不要主动劝用户刷固件**，问一句就够了。

**D. 找到设备，`halo_ring` 已经答话**

固件补丁已经在键盘上了，第 4 步整个跳过。直接第 5 步，并且在那之后跑 `reconnect`。

## 4. 外圈固件（可选，只对 Halo65 V2，需要用户动手）

### 4.1 工具链

先检查 `dfu-util`，没有它后面刷不了：

```bash
which dfu-util || brew install dfu-util
```

再看 `~/qmk_nuphy` 在不在。在就跳到 4.2。

不在的话，下面四个问题每一个都会让构建失败，照做不要绕开：

```bash
git clone --branch nuphy-keyboards https://github.com/nuphy-src/qmk_firmware ~/qmk_nuphy
cd ~/qmk_nuphy
git submodule update --init --depth 1 lib/chibios lib/chibios-contrib lib/printf lib/lufa lib/vusb
```

1. **Homebrew 的 `arm-none-eabi-gcc` 不带 newlib**，编译一定失败在 `stdint.h`。
   用 ARM 官方的 toolchain tarball，解压到 `~/qmk_nuphy/toolchain`。Apple Silicon 上
   验证过的一版是 13.3.Rel1：

   ```bash
   curl -L -o /tmp/arm-toolchain.tar.xz \
     https://developer.arm.com/-/media/Files/downloads/gnu/13.3.rel1/binrel/arm-gnu-toolchain-13.3.rel1-darwin-arm64-arm-none-eabi.tar.xz
   mkdir -p ~/qmk_nuphy/toolchain
   tar -xf /tmp/arm-toolchain.tar.xz -C ~/qmk_nuphy/toolchain --strip-components=1
   ```
2. **`brew install qmk/qmk/qmk` 会被 Homebrew 的 untrusted tap 拦住。**
   改用 venv：`python3 -m venv ~/qmk_nuphy/.venv`，然后
   `~/qmk_nuphy/.venv/bin/pip install qmk -r ~/qmk_nuphy/requirements.txt`
   （这个 fork 还依赖已被新版 qmk 换掉的 `appdirs`）。
3. 子模块只要上面那五个，`--init` 全量会拉很久。
4. 见 4.3。

### 4.2 备份改键（不许跳过）

DFU 会清空 EEPROM，用户的改键就在里面。**先备份，确认文件非空，再往下走。**

```bash
cd "$REPO"
mkdir -p backups
STAMP=$(date +%Y%m%d-%H%M%S)
./build/via_backup > backups/keymap-$STAMP.txt
grep -c '^0' backups/keymap-$STAMP.txt      # 应该是几十行，0 行就是失败
```

把 `$STAMP` 记住，4.5 要用。备份失败（键盘没答话、行数为 0）就**停下来**，不要刷。

回滚镜像（原厂固件的全片 dump）要在键盘**已经进入 DFU 之后**才能取，所以它在
4.4 之后、4.5 之前，见下面。

### 4.3 构建

```bash
cd ~/qmk_nuphy
git apply "$REPO/firmware/halo-host-control.patch"
PATH="$HOME/qmk_nuphy/toolchain/bin:$PATH" ~/qmk_nuphy/.venv/bin/qmk compile -kb nuphy/halo65_v2/ansi -km via
```

产物 `~/qmk_nuphy/nuphy_halo65_v2_ansi_via.bin`，约 68 KB（STM32F072 有 128 KB flash）。
文件不存在或明显过大就停下来，不要去刷一个没构建成功的镜像。

### 4.4 停下来，让用户进 DFU

**这一步你做不了。** 原话告诉用户：

> 现在需要你动手：**先把键盘的 USB 线拔掉**，然后**按住 Esc 键不放，同时把线插回去**。
> 松开 Esc。键盘不会亮，这是正常的 —— 它现在在 bootloader 里。
> 提醒一下：这一下会清空键盘里的改键，我刚才已经备份好了，刷完会写回去。
> 好了跟我说一声。

等用户确认。然后验证它真的在 DFU 里，**没验证到就不要刷**：

```bash
dfu-util -l | grep -i 0483:df11
```

### 4.5 先留回滚镜像，再刷

键盘现在在 DFU 里，这是唯一能 dump 原厂固件的时机。`backups/stock-firmware-backup.bin`
已经存在就跳过：

```bash
cd "$REPO"
[ -f backups/stock-firmware-backup.bin ] || \
  dfu-util -a 0 -d 0483:df11 -s 0x08000000:131072 -U backups/stock-firmware-backup.bin
```

然后刷：

```bash
dfu-util -a 0 -d 0483:df11 -s 0x08000000:leave -D ~/qmk_nuphy/nuphy_halo65_v2_ansi_via.bin
```

键盘会自己重启。等几秒，把改键写回去：

```bash
cd "$REPO"
./build/via_restore backups/keymap-$STAMP.txt      # 4.2 记下的那个
```

然后**重新扫描确认**：

```bash
/usr/bin/python3 scripts/install.py scan
```

`halo_ring` 出现在 `lighting` 里才算成功。没出现就不要往下走 —— 报告实际输出，
让用户决定是重刷还是用 `backups/stock-firmware-backup.bin` 回滚。

## 5. 安装

```bash
/usr/bin/python3 scripts/install.py install
```

它会：编译、把运行文件放进 `~/Library/Application Support/ClaudeHalo65/`、
**先用 `/bin/sh` 实跑一次 hook 自测**（这一步会挡住路径含空格导致的静默 exit 127）、
把 7 个 hook 合并进 `~/.claude/settings.json`（带时间戳备份，只动自己的条目）、
装好 LaunchAgent。

用户机器上装了 Codex 的话（`which codex` 或 `~/.codex` 存在），问一句要不要一起接上：

```bash
/usr/bin/python3 scripts/install.py codex-hooks-install
```

接的时候要说清：Codex 没有 `PostToolUseFailure` / `StopFailure`，
**所以 Codex 会话不会把灯变红**，其余状态一致。

### 5.1 设置 App

```bash
/usr/bin/python3 scripts/install.py install-app
open "$HOME/Applications/HALO.app"
```

### 5.2 外圈和远程会话（默认关着）

只有第 3 步走到 D、或第 4 步成功之后才跑这条：

```bash
/usr/bin/python3 scripts/install.py reconnect
```

它会打开外圈，并打开 Orca 桥接（远程会话）。**Orca 桥接会去轮询其他机器上终端的
标题和预览内容**，跑之前跟用户说一声；用户不想要就 `reconnect --off`，
或者只在设置 App 的「高级」页开外圈。

## 6. 验收

```bash
/usr/bin/python3 scripts/install.py status
```

逐行复述给用户，重点看：

- `service: running`
- `hooks: 7/7`
- `ring: on/off`、`firmware present` 与否 —— 跟第 3 步的分支对得上
- `keyboard:` 有读数（不是 `no response`）

不用真跑任务就能测状态机：

```bash
/usr/bin/python3 scripts/install.py send-event Stop        # 应该看到完成色
sleep 12
/usr/bin/python3 scripts/install.py send-event SessionEnd  # 应该回到默认
```

最后**必须**告诉用户这一句，否则他会以为安装失败了：

> Hook 是在 Claude Code 启动时读的。请用 **Command + Q 完全退出** Claude Code
> 再重新打开 —— 只关窗口不行。

出问题先看日志：

```bash
tail -50 ~/Library/Application\ Support/ClaudeHalo65/daemon.log
```

## 全部撤销

用户反悔时：

```bash
/usr/bin/python3 scripts/install.py uninstall
```

按标记精确移除 hook（不碰别人的），移除 LaunchAgent 和运行目录，恢复接管前的灯效。
**固件不会被回滚** —— 那要用 `backups/stock-firmware-backup.bin` 再走一次 DFU，
需要用户再动一次手。
