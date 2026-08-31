// SPDX-License-Identifier: MIT
import AppKit
import ClaudeHalo65Core
import SwiftUI

enum SidebarItem: Hashable {
    case device, hooks, advanced
    case state(String)
}

// MARK: - design system
//
// One card shape, one border, one padding, everywhere. Long explanations live
// behind an InfoTip so a page shows what a control does in a phrase and keeps
// the paragraph for whoever hovers.

enum UI {
    static let corner: CGFloat = 10
    static let cardPadding: CGFloat = 14
    static let pagePadding: CGFloat = 20
    static let pageSpacing: CGFloat = 14
    static let labelWidth: CGFloat = 56
}

/// The single container every page is built from: header line with an optional
/// icon and info tip, then the content. No card ever nests inside another.
struct Card<Content: View, Accessory: View>: View {
    let title: String
    var icon: String?
    var tip: String?
    let accessory: Accessory
    let content: Content

    init(_ title: String, icon: String? = nil, tip: String? = nil,
         @ViewBuilder accessory: () -> Accessory,
         @ViewBuilder content: () -> Content) {
        self.title = title
        self.icon = icon
        self.tip = tip
        self.accessory = accessory()
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 6) {
                if let icon {
                    Image(systemName: icon).foregroundStyle(.secondary).font(.callout)
                }
                Text(title).font(.headline)
                if let tip { InfoTip(tip) }
                Spacer(minLength: 0)
                accessory
            }
            content
        }
        .padding(UI.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: UI.corner, style: .continuous)
            .fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: UI.corner, style: .continuous)
            .strokeBorder(Color(nsColor: .separatorColor)))
    }
}

extension Card where Accessory == EmptyView {
    init(_ title: String, icon: String? = nil, tip: String? = nil,
         @ViewBuilder content: () -> Content) {
        self.init(title, icon: icon, tip: tip, accessory: { EmptyView() },
                  content: content)
    }
}

/// A tinted one-line notice, same shape as a card. For things the user should
/// see without hunting: the Bluetooth sync behaviour, a missing permission.
struct Banner<Trailing: View>: View {
    var icon: String
    var tint: Color = .blue
    let text: String
    var tip: String?
    let trailing: Trailing

    init(icon: String, tint: Color = .blue, text: String, tip: String? = nil,
         @ViewBuilder trailing: () -> Trailing = { EmptyView() }) {
        self.icon = icon
        self.tint = tint
        self.text = text
        self.tip = tip
        self.trailing = trailing()
    }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: icon).foregroundStyle(tint)
            Text(text).font(.callout)
            if let tip { InfoTip(tip) }
            Spacer(minLength: 0)
            trailing
        }
        .padding(.horizontal, UI.cardPadding).padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: UI.corner, style: .continuous)
            .fill(tint.opacity(0.09)))
    }
}

/// An info icon that shows its full text while hovered. This is where every
/// explanation longer than one line goes.
struct InfoTip: View {
    let text: String
    @State private var shown = false

    init(_ text: String) { self.text = text }

    var body: some View {
        Image(systemName: "info.circle")
            .foregroundStyle(.secondary)
            .font(.callout)
            .onHover { shown = $0 }
            .popover(isPresented: $shown, arrowEdge: .bottom) {
                Text(text)
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: 300, alignment: .leading)
                    .padding(12)
            }
    }
}

struct StatusDot: View {
    let ok: Bool
    let label: String
    var warn = false
    var tip: String?

    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(ok ? Color.green : (warn ? Color.orange : Color.red))
                .frame(width: 8, height: 8)
            Text(label).font(.callout)
            if let tip { InfoTip(tip) }
        }
    }
}

/// One session-count chip, tinted with its state's colour.
struct StatePill: View {
    let color: Color
    let text: String

    var body: some View {
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 7, height: 7)
            Text(text).font(.callout)
        }
        .padding(.horizontal, 9).padding(.vertical, 4)
        .background(Capsule().fill(color.opacity(0.13)))
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
            Text(label).frame(width: UI.labelWidth, alignment: .leading)
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
            Text("颜色").frame(width: UI.labelWidth, alignment: .leading)
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

/// A titled stepper row: "回答结束后绿色保持   [10 秒]".
struct StepperRow: View {
    let title: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    let unit: String

    var body: some View {
        HStack {
            Text(title)
            Spacer()
            Stepper("\(Int(value)) \(unit)", value: $value, in: range, step: 1)
                .font(.system(.body, design: .monospaced))
        }
    }
}

// MARK: - root

struct RootView: View {
    @EnvironmentObject var model: AppModel
    @State private var selection: SidebarItem? = .device

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
                            Circle().fill(model.stateColor(key)).frame(width: 10, height: 10)
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
            // No background of its own, so the sidebar's floating material
            // stays visible behind the two dots.
            .safeAreaInset(edge: .bottom) { sidebarStatus }
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
            .overlay(alignment: .bottom) { messageToast }
        }
        .frame(minWidth: 900, minHeight: 620)
    }

    private var sidebarStatus: some View {
        VStack(alignment: .leading, spacing: 6) {
            Divider()
            StatusDot(ok: model.daemonRunning,
                      label: model.daemonRunning ? "后台服务" : "后台服务未运行")
            let transport = model.transportLine
            StatusDot(ok: transport.ok, label: model.transportBrief, warn: transport.warn)
        }
        .font(.callout)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14).padding(.top, 4).padding(.bottom, 10)
    }

    /// Feedback ("已生效", errors) surfaces as a transient capsule over the
    /// detail pane; AppModel clears the message a few seconds after noting it.
    @ViewBuilder
    private var messageToast: some View {
        if let message = model.message {
            Text(message)
                .font(.callout)
                .foregroundStyle(model.messageIsError ? Color.red : Color.secondary)
                .lineLimit(2)
                .padding(.horizontal, 14).padding(.vertical, 7)
                .background(.regularMaterial, in: Capsule())
                .overlay(Capsule().strokeBorder(Color(nsColor: .separatorColor)))
                .padding(.bottom, 12)
                .transition(.opacity)
        }
    }
}

// MARK: - overview

struct DeviceView: View {
    @EnvironmentObject var model: AppModel

    private var currentKey: String { model.status?.state ?? "idle" }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: UI.pageSpacing) {
                Text("概览").font(.title2.bold())
                currentCard
                connectionCard
            }
            .padding(UI.pagePadding)
        }
    }

    // MARK: current state

    private var currentCard: some View {
        Card("当前状态", icon: "light.max",
             tip: "多个会话同时跑时取优先级最高的显示：语音输入 > 工具失败 > 等待权限 > "
                + "执行中 > 全部完成。只要有一个会话在等你批准就会看到琥珀色；"
                + "绿色只在全部结束时出现，且被任何新状态打断后不再回来。",
             accessory: {
                 Button {
                     model.refreshStatus()
                 } label: {
                     Image(systemName: "arrow.clockwise")
                 }
                 .buttonStyle(.borderless)
                 .help("立即刷新（平时每 3 秒自动刷新）")
             }) {
            HStack(alignment: .center, spacing: 24) {
                currentPreview
                VStack(alignment: .leading, spacing: 10) {
                    HStack(spacing: 8) {
                        Circle().fill(model.stateColor(currentKey))
                            .frame(width: 10, height: 10)
                        Text(AppSettings.displayName(currentKey)).font(.title3.bold())
                        if model.status?.previewing == true {
                            Text("预览中").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    sessionSummary
                    Button("清空会话并交还灯光") { model.resetLights() }
                        .disabled(!model.daemonRunning)
                        .help("丢弃全部跟踪中的会话，把两圈灯都交还键盘固件")
                }
                Spacer()
            }
        }
    }

    @ViewBuilder
    private var currentPreview: some View {
        let spec = model.settings.states[currentKey]
        if let halo = spec?.halo, halo.haloMode != .release {
            RingPreview(style: halo, dotSize: 5, radius: 40)
        } else {
            ZStack {
                Circle().stroke(style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
                    .foregroundStyle(.secondary)
                    .frame(width: 80, height: 80)
                Image(systemName: "keyboard").foregroundStyle(.secondary)
            }
            .frame(width: 100, height: 100)
        }
    }

    @ViewBuilder
    private var sessionSummary: some View {
        if model.sessionsByState.isEmpty {
            Text(model.daemonRunning ? "没有活跃会话。" : "后台服务未运行，读不到会话。")
                .font(.callout).foregroundStyle(.secondary)
        } else {
            HStack(spacing: 8) {
                ForEach(AppSettings.stateOrder, id: \.self) { key in
                    if let count = model.sessionsByState[key], count > 0 {
                        StatePill(color: model.stateColor(key),
                                  text: "\(AppSettings.displayName(key)) ×\(count)")
                    }
                }
            }
        }
    }

    // MARK: connection

    private var connectionCard: some View {
        Card("连接", icon: "cable.connector",
             accessory: {
                 if model.scanning {
                     ProgressView().controlSize(.small)
                 } else {
                     Button("重新扫描") { model.rescan() }.controlSize(.small)
                 }
             }) {
            VStack(alignment: .leading, spacing: 10) {
                scanSummary
                Divider()
                let transport = model.transportLine
                StatusDot(ok: transport.ok, label: transport.text, warn: transport.warn,
                          tip: "USB 下配置和状态都即时生效。蓝牙下状态即时，改配置走低速"
                             + "通道需数秒。2.4G 接收器不支持。")
                if model.isSyncing {
                    HStack(spacing: 8) {
                        ProgressView(value: Double(model.syncProgress ?? 0), total: 100)
                            .frame(width: 140)
                        Text("配置正在经蓝牙同步到键盘"
                             + (model.syncProgress.map { " \($0)%" } ?? ""))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                let ring = model.ringLine
                StatusDot(ok: ring.ok, label: ring.text, warn: ring.warn,
                          tip: "外圈有两条控制通道：USB 走 VIA，即时；蓝牙走键盘 LED 位"
                             + "通道，状态即时、改配置慢。两条都要求键盘刷过本项目的固件"
                             + "补丁，步骤见仓库的 firmware/README.md。")
                StatusDot(ok: model.daemonRunning,
                          label: model.daemonRunning ? "后台服务运行中" : "后台服务未运行")
                StatusDot(ok: model.hooksInstalled == AppModel.hookEvents.count,
                          label: "Claude Code Hooks 已装 \(model.hooksInstalled)/\(AppModel.hookEvents.count)",
                          warn: model.hooksInstalled > 0,
                          tip: "状态事件来自 Claude Code 的生命周期 hook，在左侧「Hooks」页安装。")
            }
        }
    }

    @ViewBuilder
    private var scanSummary: some View {
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
                             + " · 内圈键区背光\(device.hasMatrix ? "可用" : "不可用")"
                             + " · 外圈 Halo 环\(device.hasRing ? "可用" : "需刷固件补丁")")
                            .font(.caption).foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        } else if model.scanned != nil {
            if model.isWireless {
                Text("USB 上没有键盘，当前经蓝牙连接。插上数据线可以让改动即时生效。")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                Text("USB 上没有找到支持 VIA 的 QMK 键盘。请用数据线直连（有些线只能充电），"
                     + "并退出 VIA、NuPhy 官方软件等会独占键盘的程序。2.4G 接收器不支持。")
                    .font(.callout).foregroundStyle(.orange)
                    .fixedSize(horizontal: false, vertical: true)
            }
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
            VStack(alignment: .leading, spacing: UI.pageSpacing) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Circle().fill(model.stateColor(key)).frame(width: 12, height: 12)
                        Text(AppSettings.displayName(key)).font(.title2.bold())
                        InfoTip(AppSettings.detail(key))
                    }
                    Text(AppSettings.explanation(key))
                        .font(.callout).foregroundStyle(.secondary)
                }

                if model.isWireless { wirelessBanner }

                if key == "completed" {
                    Card("熄灭倒计时", icon: "timer",
                         tip: "这是真正的倒计时：到点绿色熄灭、回到默认状态。期间任何"
                            + "新状态都会立即打断它，打断后绿色不再回来。") {
                        StepperRow(title: "回答结束后绿色保持",
                                   value: $model.settings.completedHoldSeconds,
                                   range: 1...30, unit: "秒")
                    }
                }
                if key == "failure" {
                    Card("红色保护窗", icon: "timer",
                         tip: "保护窗内，后续的「执行中」事件不会把红色顶掉，让失败来得及"
                            + "被看见。到点后红色不会自己熄灭，而是等下一个事件切换走。") {
                        StepperRow(title: "失败后红色至少保持",
                                   value: $model.settings.failureHoldSeconds,
                                   range: 1...30, unit: "秒")
                    }
                }
                if key == "voice" { triggerCard() }

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
                        Text("外圈预览").font(.caption).foregroundStyle(.secondary)
                        Button("在键盘上预览 3 秒") { model.preview(key) }
                            .disabled(!model.daemonRunning || !state.wrappedValue.isValid)
                    }
                    .frame(width: 180)

                    VStack(alignment: .leading, spacing: UI.pageSpacing) {
                        haloCard(state)
                        matrixCard(state)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(UI.pagePadding)
        }
    }

    private var wirelessBanner: some View {
        Banner(icon: "antenna.radiowaves.left.and.right",
               text: "键盘经蓝牙连接：改动在松手后同步到键盘，约需几秒。",
               tip: "状态切换仍然即时。改配置走每秒约 16 bit 的 LED 位通道，改一种颜色"
                  + "约 8 秒。「在键盘上预览」显示键盘里已存的参数，同步完成前可能还是"
                  + "旧值。插上 USB 数据线则一切即时生效。") {
            if model.isSyncing {
                HStack(spacing: 6) {
                    ProgressView(value: Double(model.syncProgress ?? 0), total: 100)
                        .frame(width: 90)
                    Text(model.syncProgress.map { "\($0)%" } ?? "同步中")
                        .font(.caption).foregroundStyle(.secondary)
                }
            } else {
                Label("已同步", systemImage: "checkmark.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    // MARK: voice trigger

    private func modifierBinding(_ name: String) -> Binding<Bool> {
        Binding(get: { model.settings.voice.modifiers.contains(name) },
                set: { on in
                    var mods = Set(model.settings.voice.modifiers)
                    if on { mods.insert(name) } else { mods.remove(name) }
                    // Kept in a fixed order so the file does not churn and the
                    // shortcut always reads ⌃⌥⇧⌘.
                    model.settings.voice.modifiers =
                        VoiceConfig.modifierOrder.filter { mods.contains($0) }
                })
    }

    /// tail_seconds is a Double in the file; the slider works in milliseconds so
    /// it lands on round numbers.
    private var tailMilliseconds: Binding<Int> {
        Binding(get: { Int((model.settings.voice.tailSeconds * 1000).rounded()) },
                set: { model.settings.voice.tailSeconds = Double($0) / 1000 })
    }

    private var watcherLine: (ok: Bool, warn: Bool, text: String) {
        let voice = model.settings.voice
        guard voice.enabled else { return (false, true, "未开启") }
        switch model.status?.voice?.watcher {
        case "running":
            return (true, false, "监听进程在运行")
        case "needs input monitoring":
            return (false, true, "缺「输入监控」权限，快捷键收不到")
        case "disabled":
            return (false, true, "监听进程还没读到新配置，稍等一秒")
        case .some(let other):
            return (false, true, "监听进程：\(other)")
        case nil:
            return (false, false, "后台服务没在答话")
        }
    }

    @ViewBuilder
    private func triggerCard() -> some View {
        let voice = $model.settings.voice
        let status = watcherLine
        Card("触发方式", icon: "mic",
             tip: "监听进程只比对配置的这一个组合键，不认识也不上报别的按键；"
                + "麦克风检测只读设备属性，不打开音频流，任何应用录音都会触发。") {
            VStack(alignment: .leading, spacing: 12) {
                Toggle("开启语音输入灯效", isOn: voice.enabled)

                HStack(spacing: 10) {
                    Text("触发").frame(width: UI.labelWidth, alignment: .leading)
                    Picker("", selection: voice.trigger) {
                        Text("快捷键").tag("hotkey")
                        Text("麦克风被占用").tag("microphone")
                        Text("两者任一").tag("both")
                    }
                    .labelsHidden()
                    .frame(width: 190)
                    if model.settings.voice.trigger != "hotkey" {
                        InfoTip("麦克风这条不需要任何权限，但任何应用开始录音都会亮。")
                    }
                }

                if model.settings.voice.trigger != "microphone" {
                    HStack(spacing: 8) {
                        Text("快捷键").frame(width: UI.labelWidth, alignment: .leading)
                        ForEach(VoiceConfig.modifierOrder, id: \.self) { name in
                            Toggle(VoiceConfig.modifierSymbols[name] ?? name,
                                   isOn: modifierBinding(name))
                                .toggleStyle(.button)
                        }
                        Picker("", selection: voice.keycode) {
                            ForEach(VoiceConfig.keys, id: \.code) { key in
                                Text(key.name).tag(key.code)
                            }
                        }
                        .labelsHidden()
                        .frame(width: 110)
                        Text(model.settings.voice.shortcutDescription)
                            .font(.system(.body, design: .monospaced))
                            .foregroundStyle(.secondary)
                        InfoTip("按事件看到的样子填，不是按键帽上印的字。系统设置里把 "
                              + "Control 和 Command 对调过的话，物理 Command 键发出来"
                              + "的是 ⌃，这里就要选 ⌃。")
                    }

                    HStack(spacing: 10) {
                        Text("方式").frame(width: UI.labelWidth, alignment: .leading)
                        Picker("", selection: voice.mode) {
                            Text("按住").tag("hold")
                            Text("按一下切换").tag("toggle")
                        }
                        .pickerStyle(.segmented)
                        .labelsHidden()
                        .frame(width: 210)
                        Text(model.settings.voice.mode == "hold"
                             ? "松开就结束" : "再按一下才结束")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                HStack(spacing: 10) {
                    LabeledSlider(label: "余留", value: tailMilliseconds,
                                  range: 0...5000, suffix: " ms")
                    InfoTip("触发结束后灯再保持这么久，盖住语音转文字落盘那一下的延迟。")
                }

                Divider()
                HStack(spacing: 10) {
                    StatusDot(ok: status.ok, label: status.text, warn: status.warn)
                    Spacer()
                    if model.status?.voice?.watcher == "needs input monitoring" {
                        Button("打开输入监控设置") {
                            if let url = URL(string: "x-apple.systempreferences:"
                                             + "com.apple.preference.security?Privacy_ListenEvent") {
                                NSWorkspace.shared.open(url)
                            }
                        }
                    }
                }
                if model.status?.voice?.watcher == "needs input monitoring" {
                    Text("把 ~/Library/Application Support/ClaudeHalo65/halo65_voice "
                         + "加进列表并打开开关，然后在终端跑一次 install.py voice。")
                        .font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: halo / matrix

    @ViewBuilder
    private func haloCard(_ state: Binding<StateSpec>) -> some View {
        Card("外圈 · Halo 环", icon: "circle.dashed",
             tip: "底座一圈 50 颗灯，作为一个整体显示状态。需要刷过本项目的固件补丁。") {
            VStack(alignment: .leading, spacing: 12) {
                if !model.settings.zones.halo {
                    Label("外圈已在「高级」页关闭，这些设置暂不生效。",
                          systemImage: "exclamationmark.triangle")
                        .font(.callout).foregroundStyle(.orange)
                }
                HStack(spacing: 10) {
                    Text("动画").frame(width: UI.labelWidth, alignment: .leading)
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
        }
    }

    @ViewBuilder
    private func matrixCard(_ state: Binding<StateSpec>) -> some View {
        let following = state.wrappedValue.matrix.followColor
        let live = !(isIdle && state.wrappedValue.matrix.restore)
        let picked = MatrixSpec.effect(state.wrappedValue.matrix.effect)

        Card("内圈 · 键区背光", icon: "keyboard",
             tip: "打字区的 RGB 背光，任何支持 VIA 的 QMK 键盘都能驱动。"
                + "42 个灯效来自 QMK 自带的效果表。") {
            VStack(alignment: .leading, spacing: 12) {
                if !model.settings.zones.matrix {
                    Label("内圈已在「高级」页关闭，这些设置暂不生效。",
                          systemImage: "exclamationmark.triangle")
                        .font(.callout).foregroundStyle(.orange)
                }
                if isIdle {
                    HStack(spacing: 8) {
                        Toggle("恢复我自己的键区灯效（推荐）", isOn: state.matrix.restore)
                        InfoTip("开启时，空闲会把键区还原成接管前的样子。"
                              + "关掉则改用下面指定的固定灯效。")
                    }
                }

                Group {
                    HStack(spacing: 10) {
                        Text("灯效").frame(width: UI.labelWidth, alignment: .leading)
                        Picker("", selection: state.matrix.effect) {
                            Section("使用下面的颜色") {
                                ForEach(MatrixSpec.effects.filter(\.honoursColor)) { effect in
                                    Text("\(effect.id) · \(effect.name)").tag(effect.id)
                                }
                            }
                            Section("自带色相动画（忽略颜色）") {
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
                        Text("正在跟随外圈的 \(state.wrappedValue.halo.color)。"
                             + "关掉上面的开关即可单独设色。")
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
        }
    }
}

// MARK: - hooks

struct HooksView: View {
    @EnvironmentObject var model: AppModel

    /// What each event does to the lights, so the list explains itself.
    private static let eventNotes: [String: String] = [
        "UserPromptSubmit": "提交问题 → 执行中",
        "PermissionRequest": "请求批准 → 等待权限",
        "PostToolUse": "工具完成 → 执行中",
        "PostToolUseFailure": "工具失败 → 工具失败",
        "Stop": "回答结束 → 全部完成",
        "StopFailure": "回答出错 → 工具失败",
        "SessionEnd": "会话退出 → 移除该会话",
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: UI.pageSpacing) {
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 8) {
                        Text("Hooks").font(.title2.bold())
                        InfoTip("安装会把 7 个条目合并进 ~/.claude/settings.json：先做"
                              + "带时间戳的备份，只增删本项目自己的条目，不碰别人的。"
                              + "hook 只把事件名和会话 ID 转发给本机的后台服务，"
                              + "提示词和工具输出都不经过它。")
                    }
                    Text("状态事件来自 Claude Code 的生命周期 hook，装好后灯才会跟着任务变。")
                        .font(.callout).foregroundStyle(.secondary)
                }

                Card("安装状态", icon: "link",
                     accessory: {
                         Text("\(model.hooksInstalled)/\(AppModel.hookEvents.count)")
                             .font(.body.monospaced()).foregroundStyle(.secondary)
                     }) {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(AppModel.hookEvents, id: \.self) { event in
                            let installed = model.installedHookEvents.contains(event)
                            HStack(spacing: 8) {
                                Image(systemName: installed ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(installed ? .green : .secondary)
                                Text(event).font(.system(.callout, design: .monospaced))
                                Spacer()
                                Text(Self.eventNotes[event] ?? "")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        Divider()
                        HStack(spacing: 10) {
                            Button(model.hooksInstalled == AppModel.hookEvents.count
                                   ? "重新安装" : "安装 Hooks") {
                                model.runInstaller("hooks-install")
                            }
                            .buttonStyle(.borderedProminent)
                            Button("卸载") { model.runInstaller("hooks-uninstall") }
                                .disabled(model.hooksInstalled == 0)
                            Spacer()
                        }
                        Text("改动在 Claude Code 完全退出并重新打开后生效"
                             + "（Command + Q，只关窗口不算）。")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .padding(UI.pagePadding)
        }
    }
}

// MARK: - advanced

struct AdvancedView: View {
    @EnvironmentObject var model: AppModel
    @State private var confirmRestore = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: UI.pageSpacing) {
                Text("高级").font(.title2.bold())

                Card("外圈与远程会话", icon: "dot.radiowaves.left.and.right",
                     tip: "「重新连接」先在 USB 上探测外圈固件，探测到就打开外圈，同时"
                        + "打开 Orca 桥接。蓝牙下探测不到 VIA，已开启的外圈不会被它关掉。"
                        + "Orca 桥接会轮询其他机器上终端的标题和预览来判断远程会话的状态。") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("默认只驱动键区背光、只看本机会话。外圈需要刷过固件补丁，"
                             + "远程会话需要轮询其他机器，所以这两件事都做成主动开启。")
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
                        if !model.haloControllable {
                            Text("现在读不到外圈：要么键盘未连接，要么 VIA / NuPhy 官方软件"
                                 + "占着接口，要么固件还是原厂的。刷固件的步骤在仓库的 "
                                 + "firmware/README.md。")
                                .font(.caption).foregroundStyle(.orange)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }

                Card("驱动区域", icon: "square.grid.2x2",
                     tip: "关掉的区域完全不碰：既不读也不写，也不会去恢复它。"
                        + "两个区域可以同时开，触发状态时一起变。") {
                    VStack(alignment: .leading, spacing: 8) {
                        Toggle("外圈 · Halo 环", isOn: $model.settings.zones.halo)
                            .disabled(!model.haloControllable)
                        Toggle("内圈 · 键区背光", isOn: $model.settings.zones.matrix)
                    }
                }

                Card("会话清理", icon: "clock.arrow.circlepath",
                     tip: "被强杀的 Claude Code 不会发出 SessionEnd，对应会话会一直留在"
                        + "「执行中」。超过这个时长没有任何事件的活跃会话会被丢弃，"
                        + "免得灯一直转。正常工作的会话每次工具调用都会刷新计时。") {
                    StepperRow(title: "活跃会话无事件超过此时长即丢弃",
                               value: $model.settings.staleActiveMinutes,
                               range: 5...240, unit: "分钟")
                }

                Card("配置文件", icon: "doc.text",
                     tip: "改动即时写入并生效。「重新读取」在手工编辑过文件后使用；"
                        + "「恢复默认」把全部设置还原为出厂值。") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text(SettingsStore.url.path)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                        HStack(spacing: 10) {
                            Button("在访达中显示") {
                                NSWorkspace.shared.activateFileViewerSelecting([SettingsStore.url])
                            }
                            Button("重新读取") { model.reload() }
                            Button("恢复默认") { confirmRestore = true }
                            Spacer()
                        }
                    }
                }
            }
            .padding(UI.pagePadding)
        }
        .confirmationDialog("把全部设置恢复为默认值？", isPresented: $confirmRestore) {
            Button("恢复默认", role: .destructive) { model.restoreDefaults() }
            Button("取消", role: .cancel) {}
        } message: {
            Text("六个状态的灯效、语音输入和时间设置都会被还原。")
        }
    }
}
