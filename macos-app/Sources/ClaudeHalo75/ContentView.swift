// SPDX-License-Identifier: MIT
import AppKit
import ClaudeHalo75Core
import SwiftUI

enum SidebarItem: Hashable {
    case device, hooks, advanced
    case state(String)
}

// MARK: - shared bits

struct StatusDot: View {
    let ok: Bool
    let label: String
    var warn = false

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(ok ? Color.green : (warn ? Color.orange : Color.red))
                .frame(width: 8, height: 8)
            Text(label).font(.callout)
        }
    }
}

struct LabeledSlider: View {
    let label: String
    @Binding var value: Int
    let range: ClosedRange<Double>
    var suffix = ""
    var enabled = true

    var body: some View {
        HStack(spacing: 10) {
            Text(label).frame(width: 52, alignment: .leading)
            Slider(value: Binding(get: { Double(value) },
                                  set: { value = Int($0.rounded()) }), in: range)
            Text("\(value)\(suffix)")
                .font(.system(.body, design: .monospaced))
                .frame(width: 62, alignment: .trailing)
                .foregroundStyle(.secondary)
        }
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.45)
    }
}

struct ColorField: View {
    @Binding var hex: String
    var enabled = true

    private var swatch: Binding<Color> {
        Binding(
            get: {
                guard let rgb = ColorHex.parse(hex) else { return .gray }
                return Color(.sRGB, red: Double(rgb.r) / 255,
                             green: Double(rgb.g) / 255, blue: Double(rgb.b) / 255)
            },
            set: { newValue in
                let ns = NSColor(newValue).usingColorSpace(.sRGB) ?? .white
                hex = ColorHex.string(r: Int(ns.redComponent * 255),
                                      g: Int(ns.greenComponent * 255),
                                      b: Int(ns.blueComponent * 255))
            }
        )
    }

    var body: some View {
        HStack(spacing: 10) {
            Text("颜色").frame(width: 52, alignment: .leading)
            ColorPicker("", selection: swatch, supportsOpacity: false).labelsHidden()
            TextField("#RRGGBB", text: $hex)
                .textFieldStyle(.roundedBorder)
                .font(.system(.body, design: .monospaced))
                .frame(width: 110)
                .foregroundStyle(ColorHex.parse(hex) == nil ? Color.red : Color.primary)
            Spacer()
        }
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.45)
    }
}

// MARK: - root

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @State private var selection: SidebarItem? = .state("running")

    var body: some View {
        NavigationSplitView {
            List(selection: $selection) {
                Section("设备") {
                    Label("概览", systemImage: "keyboard").tag(SidebarItem.device)
                }
                Section("状态灯") {
                    ForEach(AppSettings.stateOrder, id: \.self) { key in
                        Label {
                            Text(AppSettings.displayName(key))
                        } icon: {
                            Circle().fill(colorFor(key)).frame(width: 10, height: 10)
                        }
                        .tag(SidebarItem.state(key))
                    }
                }
                Section("系统") {
                    Label("Hooks", systemImage: "link").tag(SidebarItem.hooks)
                    Label("高级", systemImage: "gearshape").tag(SidebarItem.advanced)
                }
            }
            .navigationSplitViewColumnWidth(min: 175, ideal: 190, max: 230)
        } detail: {
            Group {
                switch selection ?? .device {
                case .device:         DeviceView()
                case .state(let key): StateDetailView(key: key)
                case .hooks:          HooksView()
                case .advanced:       AdvancedView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .frame(minWidth: 900, minHeight: 620)
        .safeAreaInset(edge: .bottom) { footer }
    }

    private func colorFor(_ key: String) -> Color {
        guard let spec = model.settings.states[key] else { return .gray }
        if spec.halo.haloMode == .release && spec.matrix.restore { return .secondary.opacity(0.5) }
        guard let rgb = ColorHex.parse(spec.halo.color) else { return .gray }
        return Color(.sRGB, red: Double(rgb.r) / 255,
                     green: Double(rgb.g) / 255, blue: Double(rgb.b) / 255)
    }

    private var footer: some View {
        HStack(spacing: 10) {
            if let message = model.message {
                Text(message)
                    .font(.callout)
                    .foregroundStyle(model.messageIsError ? Color.red : Color.secondary)
                    .lineLimit(2)
            }
            Spacer()
            if model.isDirty { Text("有未保存的修改").font(.callout).foregroundStyle(.orange) }
            Button("恢复默认") { model.restoreDefaults() }
            Button("重新读取") { model.reload() }
            Button("保存并应用") { model.save() }
                .keyboardShortcut("s")
                .buttonStyle(.borderedProminent)
                .disabled(!model.canSave)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(.bar)
    }
}

// MARK: - device

struct DeviceView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("设备概览").font(.title2.bold())

                GroupBox {
                    VStack(alignment: .leading, spacing: 10) {
                        if model.scanning {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text("正在扫描 USB…").font(.callout).foregroundStyle(.secondary)
                            }
                        } else if let devices = model.scanned, !devices.isEmpty {
                            ForEach(devices) { device in
                                VStack(alignment: .leading, spacing: 4) {
                                    HStack(spacing: 8) {
                                        Text(device.displayName).font(.body.bold())
                                        Text("\(device.vendorId):\(device.productId)")
                                            .font(.caption.monospaced()).foregroundStyle(.secondary)
                                    }
                                    if !device.reachable {
                                        Text(device.error ?? "无法打开 VIA 接口")
                                            .font(.caption).foregroundStyle(.orange)
                                            .fixedSize(horizontal: false, vertical: true)
                                    } else {
                                        Text("VIA 协议 \(device.viaProtocol.map(String.init) ?? "?")"
                                             + " · 内圈 RGB Matrix \(device.hasMatrix ? "可用" : "无")"
                                             + " · 外圈 Halo 环 \(device.hasRing ? "可用" : "需刷固件补丁")")
                                            .font(.caption).foregroundStyle(.secondary)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                        } else if model.scanned != nil {
                            Text("USB 上没有找到会说 VIA 的 QMK 键盘。"
                                 + "2.4G 接收器和蓝牙都不暴露 VIA 接口，只有 USB 数据线可以"
                                 + "（有些线只能充电）。")
                                .font(.callout).foregroundStyle(.orange)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Divider()
                        StatusDot(ok: model.daemonRunning,
                                  label: model.daemonRunning ? "后台服务运行中" : "后台服务未运行")
                        StatusDot(ok: model.haloSupported,
                                  label: model.haloSupported
                                      ? "外圈可控（固件 VIA 通道 0x10）"
                                      : "外圈不可控 —— 固件未包含 Halo 补丁", warn: true)
                        StatusDot(ok: model.hooksInstalled == 7,
                                  label: "Claude Code Hooks \(model.hooksInstalled)/7",
                                  warn: model.hooksInstalled > 0)
                    }
                    .padding(6)
                } label: { Label("键盘", systemImage: "keyboard") }

                GroupBox {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 24) {
                            labelled("当前显示", AppSettings.displayName(model.status?.state ?? "idle"))
                            labelled("跟踪中的会话", "\(model.status?.sessions ?? 0)")
                            labelled("服务版本", model.status?.version ?? "—")
                        }
                        if model.sessionsByState.isEmpty {
                            Text("没有活跃会话。").font(.callout).foregroundStyle(.secondary)
                        } else {
                            Divider()
                            ForEach(AppSettings.stateOrder, id: \.self) { key in
                                if let n = model.sessionsByState[key], n > 0 {
                                    HStack(spacing: 8) {
                                        Text("\(n)").font(.body.monospaced().bold())
                                            .frame(width: 20, alignment: .trailing)
                                        Text("个会话处于「\(AppSettings.displayName(key))」")
                                            .font(.callout)
                                        Spacer()
                                    }
                                }
                            }
                        }
                        Text("多个会话同时跑时，取优先级最高的显示："
                             + "失败 > 等待权限 > 执行中 > 全部完成 > 默认。"
                             + "所以只要有任何一个要你批准就会看到，而绿色只在全部结束时出现。")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(6)
                } label: { Label("会话", systemImage: "square.stack.3d.up") }

                GroupBox {
                    HStack(spacing: 10) {
                        Button("清空会话并交还灯光") { model.resetLights() }
                            .disabled(!model.daemonRunning)
                        Button("重新扫描键盘") { model.rescan() }
                            .disabled(model.scanning)
                        Button("刷新状态") { model.refreshStatus() }
                        Spacer()
                    }
                    .padding(6)
                } label: { Text("操作") }
            }
            .padding(20)
        }
    }

    private func labelled(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.body.monospaced())
        }
    }
}

// MARK: - one state

struct StateDetailView: View {
    @EnvironmentObject var model: AppModel
    let key: String

    private var spec: Binding<StateSpec> {
        Binding(get: { model.settings.states[key] ?? AppSettings.defaults.states[key]! },
                set: { model.settings.states[key] = $0 })
    }
    private var isIdle: Bool { key == "idle" }

    var body: some View {
        let state = spec

        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(AppSettings.displayName(key)).font(.title2.bold())
                    Text(AppSettings.explanation(key))
                        .font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                HStack(alignment: .top, spacing: 24) {
                    VStack(spacing: 8) {
                        if state.wrappedValue.halo.haloMode == .release {
                            ZStack {
                                Circle().stroke(style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
                                    .foregroundStyle(.secondary)
                                    .frame(width: 124, height: 124)
                                Text("交还固件").font(.callout).foregroundStyle(.secondary)
                            }
                            .frame(width: 152, height: 152)
                        } else {
                            RingPreview(style: state.wrappedValue.halo)
                        }
                        Text("外圈界面预览").font(.caption).foregroundStyle(.secondary)
                        Button("在键盘上预览 3 秒") { model.preview(key) }
                            .disabled(!model.daemonRunning || !state.wrappedValue.isValid)
                    }

                    VStack(alignment: .leading, spacing: 16) {
                        haloBox(state)
                        matrixBox(state)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(20)
        }
    }

    @ViewBuilder
    private func haloBox(_ state: Binding<StateSpec>) -> some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                if !model.settings.zones.halo {
                    Text("外圈在「高级」里被关闭了，这些设置不会生效。")
                        .font(.callout).foregroundStyle(.orange)
                }
                HStack(spacing: 10) {
                    Text("动画").frame(width: 52, alignment: .leading)
                    Picker("", selection: state.halo.mode) {
                        ForEach(isIdle ? HaloMode.allCases : HaloMode.activeCases, id: \.rawValue) {
                            Text($0.displayName).tag($0.rawValue)
                        }
                    }
                    .labelsHidden().frame(width: 170)
                    Spacer()
                }
                let live = state.wrappedValue.halo.haloMode != .release
                ColorField(hex: state.halo.color, enabled: live)
                LabeledSlider(label: "亮度", value: state.halo.brightness,
                              range: 0...100, suffix: "%", enabled: live)
                LabeledSlider(label: "速度", value: state.halo.speed,
                              range: 0...255, enabled: live)
                if let paramLabel = state.wrappedValue.halo.haloMode.paramLabel {
                    LabeledSlider(label: paramLabel, value: state.halo.param,
                                  range: state.wrappedValue.halo.haloMode.paramRange,
                                  suffix: state.wrappedValue.halo.haloMode == .comet ? " 颗" : "%",
                                  enabled: live)
                }
            }
            .padding(6)
        } label: { Label("外圈 · Halo 环形灯（45 颗）", systemImage: "circle.dashed") }
    }

    @ViewBuilder
    private func matrixBox(_ state: Binding<StateSpec>) -> some View {
        let following = state.wrappedValue.matrix.followColor
        let live = !(isIdle && state.wrappedValue.matrix.restore)
        let picked = MatrixSpec.effect(state.wrappedValue.matrix.effect)

        GroupBox {
            VStack(alignment: .leading, spacing: 12) {
                if !model.settings.zones.matrix {
                    Text("内圈在「高级」里被关闭了，这些设置不会生效。")
                        .font(.callout).foregroundStyle(.orange)
                }
                if isIdle {
                    Toggle("恢复我自己的键区灯效（推荐）", isOn: state.matrix.restore)
                    Text("开启时，空闲会把键区还原成接管前的样子。关掉则改用下面指定的固定灯效。")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Group {
                    HStack(spacing: 10) {
                        Text("灯效").frame(width: 52, alignment: .leading)
                        Picker("", selection: state.matrix.effect) {
                            Section("认颜色") {
                                ForEach(MatrixSpec.effects.filter(\.honoursColor)) { effect in
                                    Text("\(effect.id) · \(effect.name)").tag(effect.id)
                                }
                            }
                            Section("自带色相动画（会忽略颜色）") {
                                ForEach(MatrixSpec.effects.filter { !$0.honoursColor }) { effect in
                                    Text("\(effect.id) · \(effect.name)").tag(effect.id)
                                }
                            }
                        }
                        .labelsHidden().frame(width: 250)
                        Spacer()
                    }

                    if let picked, !picked.honoursColor {
                        Label("这个灯效自己控制色相，下面设的颜色不会生效。",
                              systemImage: "info.circle")
                            .font(.caption).foregroundStyle(.orange)
                    }

                    Toggle("跟随外圈颜色", isOn: state.matrix.followColor)

                    // Kept visible, not swapped for a label: hiding the control
                    // made it look as though the colour simply was not settable.
                    ColorField(hex: following ? state.halo.color : state.matrix.color,
                               enabled: !following)
                    if following {
                        Text("正在跟随外圈的 \(state.wrappedValue.halo.color)。关掉上面的开关即可单独设色。")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    LabeledSlider(label: "亮度", value: state.matrix.brightness,
                                  range: 0...100, suffix: "%")
                    LabeledSlider(label: "速度", value: state.matrix.speed, range: 0...255)
                }
                .disabled(!live)
                .opacity(live ? 1 : 0.45)
            }
            .padding(6)
        } label: { Label("内圈 · 键区背光（42 个灯效）", systemImage: "keyboard") }
    }
}

// MARK: - hooks

struct HooksView: View {
    @EnvironmentObject var model: AppModel
    private let events = ["UserPromptSubmit", "PermissionRequest", "PostToolUse",
                          "PostToolUseFailure", "Stop", "StopFailure", "SessionEnd"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("Claude Code Hooks").font(.title2.bold())
                Text("状态来自 Claude Code 的生命周期 hook。安装会把 7 个条目合并进 "
                     + "~/.claude/settings.json，先做带时间戳的备份，且只增删本项目自己的条目。"
                     + "改完需要完全退出并重新打开 Claude Code 才生效。")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                GroupBox {
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(events, id: \.self) { event in
                            HStack(spacing: 8) {
                                Image(systemName: model.hooksInstalled == 7
                                      ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(model.hooksInstalled == 7 ? .green : .secondary)
                                Text(event).font(.system(.callout, design: .monospaced))
                                Spacer()
                            }
                        }
                    }
                    .padding(6)
                } label: { Text("已安装 \(model.hooksInstalled)/7") }

                HStack(spacing: 10) {
                    Button(model.hooksInstalled == 7 ? "重新安装" : "安装 Hooks") {
                        model.runInstaller("hooks-install")
                    }
                    .buttonStyle(.borderedProminent)
                    Button("卸载 Hooks") { model.runInstaller("hooks-uninstall") }
                        .disabled(model.hooksInstalled == 0)
                    Spacer()
                }
            }
            .padding(20)
        }
    }
}

// MARK: - advanced

struct AdvancedView: View {
    @EnvironmentObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                Text("高级").font(.title2.bold())

                GroupBox("重新连接键盘") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("默认只驱动内圈（键区背光），本机会话。这两件事一台原厂键盘做不到，"
                             + "所以做成主动开启：外圈需要刷入本项目的固件，Orca 桥接会去轮询"
                             + "别的机器上的终端。点一下就一起打开。")
                            .font(.callout).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                        HStack(spacing: 10) {
                            Button("重新连接（开启外圈 + Orca）") {
                                model.runInstaller("reconnect")
                                model.reload()
                            }
                            Button("退回默认") {
                                model.runInstaller("reconnect", "--off")
                                model.reload()
                            }
                            Spacer()
                        }
                        if !model.haloSupported {
                            Text("现在读不到外圈：要么键盘不是 USB 直连（2.4G / 蓝牙不暴露 VIA），"
                                 + "要么 VIA / NuPhy 官方软件占着 raw HID，要么固件还是原厂的。"
                                 + "刷固件的步骤在项目 firmware/README.md。")
                                .font(.caption).foregroundStyle(.orange)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .padding(6)
                }

                GroupBox("驱动哪些区域") {
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle("外圈 · Halo 环形灯", isOn: $model.settings.zones.halo)
                            .disabled(!model.haloSupported)
                        Toggle("内圈 · 键区背光", isOn: $model.settings.zones.matrix)
                        Text("两个可以同时开 —— 触发状态时一起变色。关掉的区域完全不碰："
                             + "既不读也不写，更不会去恢复它。")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(6)
                }

                GroupBox("时间") {
                    VStack(alignment: .leading, spacing: 12) {
                        stepper("完成后保持", $model.settings.completedHoldSeconds, 1...120, "秒",
                                "回答结束后绿色保持多久，然后回到默认状态。")
                        stepper("失败闪烁保持", $model.settings.failureHoldSeconds, 1...60, "秒",
                                "红色闪多久后自动回到「执行中」。")
                        stepper("失效会话清理", $model.settings.staleActiveMinutes, 5...240, "分钟",
                                "被强杀的 Claude Code 不会发出 SessionEnd。超过这个时间没有任何事件的活跃会话会被丢弃，"
                                + "免得灯一直卡在「执行中」。")
                    }
                    .padding(6)
                }

                GroupBox("配置文件") {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(SettingsStore.url.path)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                        Button("在访达中显示") {
                            NSWorkspace.shared.activateFileViewerSelecting([SettingsStore.url])
                        }
                    }
                    .padding(6)
                }
            }
            .padding(20)
        }
    }

    private func stepper(_ title: String, _ value: Binding<Double>,
                         _ range: ClosedRange<Double>, _ unit: String,
                         _ note: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(title)
                Spacer()
                Stepper("\(Int(value.wrappedValue)) \(unit)", value: value, in: range, step: 1)
            }
            Text(note).font(.caption).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
