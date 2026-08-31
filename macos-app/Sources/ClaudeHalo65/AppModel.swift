// SPDX-License-Identifier: MIT
import ClaudeHalo65Core
import Combine
import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var settings: AppSettings = .defaults
    @Published var saved: AppSettings = .defaults
    @Published var status: DaemonStatus?
    @Published var message: String?
    @Published var messageIsError = false
    @Published var installedHookEvents: Set<String> = []
    /// nil until the first scan finishes, or when via_scan is not installed;
    /// an empty array means the scan ran and found no QMK keyboard on USB.
    @Published var scanned: [ScannedDevice]?
    @Published var scanning = false

    private var pollTimer: Timer?
    private var autosave: AnyCancellable?

    var isDirty: Bool { settings != saved }
    var daemonRunning: Bool { status?.ok == true }
    var hooksInstalled: Int { installedHookEvents.count }
    /// The VIA halo channel answered just now, i.e. USB with patched firmware.
    var haloSupported: Bool { status?.haloSupported == true }
    var sessionsByState: [String: Int] { status?.sessionsByState ?? [:] }

    /// "usb" / "bluetooth" / "none", as the daemon judged on its last poll.
    var transport: String { status?.transport ?? "none" }
    var isWireless: Bool { transport == "bluetooth" }
    var isSyncing: Bool { status?.wireless?.syncing == true }
    var syncProgress: Int? {
        guard let p = status?.wireless?.syncProgress, p >= 0 else { return nil }
        return p
    }

    /// Whether the ring can be driven right now, over either channel. VIA is
    /// only reachable over USB, so haloSupported alone would call a Bluetooth
    /// keyboard "uncontrollable" while its ring is following states just fine
    /// through the LED-bit channel.
    var haloControllable: Bool { haloSupported || isWireless }

    var transportLine: (ok: Bool, warn: Bool, text: String) {
        switch transport {
        case "usb":
            return (true, false, "USB 有线连接：改动即时生效")
        case "bluetooth":
            return (true, false, "蓝牙连接：状态即时，改配置需数秒同步")
        default:
            return (false, false, "键盘未连接（USB 和蓝牙都不可达）")
        }
    }

    /// Two or three words for the status bar; the full story is transportLine.
    var transportBrief: String {
        switch transport {
        case "usb":       return "USB 有线"
        case "bluetooth": return "蓝牙"
        default:          return "键盘未连接"
        }
    }

    var ringLine: (ok: Bool, warn: Bool, text: String) {
        if haloSupported { return (true, false, "外圈可控 · USB（VIA 通道）") }
        if isWireless { return (true, false, "外圈可控 · 蓝牙（LED 位通道）") }
        if transport == "usb" {
            return (false, true, "外圈不可控：固件没有 Halo 补丁")
        }
        return (false, false, "外圈不可控：键盘未连接")
    }

    /// A state's colour, for sidebar dots and session chips. The handed-back
    /// idle look has no colour of its own and shows as neutral grey.
    func stateColor(_ key: String) -> Color {
        guard let spec = settings.states[key] else { return .gray }
        if spec.halo.haloMode == .release && spec.matrix.restore {
            return .secondary.opacity(0.5)
        }
        guard let rgb = ColorHex.parse(spec.halo.color) else { return .gray }
        return Color(.sRGB, red: Double(rgb.r) / 255,
                     green: Double(rgb.g) / 255, blue: Double(rgb.b) / 255)
    }

    init() {
        reload()
        refreshStatus()
        rescan()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refreshStatus() }
        }
        // Every edit applies itself. A slider dragged across its range publishes
        // on every frame, so the write is debounced rather than fired per value;
        // a quarter second is below the threshold where a change feels delayed
        // and far above the rate at which settings.json would be rewritten.
        autosave = $settings
            .dropFirst()
            .debounce(for: .milliseconds(250), scheduler: RunLoop.main)
            .sink { [weak self] _ in self?.save() }
    }

    // MARK: - settings

    func reload() {
        do {
            let loaded = try SettingsStore.load()
            settings = loaded
            saved = loaded
            note("已从磁盘重新读取")
        } catch {
            // A missing file is the normal first-run case, not a failure.
            settings = .defaults
            saved = .defaults
            if (error as NSError).code != NSFileNoSuchFileError {
                note("读取配置失败：\(error.localizedDescription)", isError: true)
            }
        }
    }

    /// Writes settings.json and tells the daemon to pick it up. Called by the
    /// autosave pipeline, and directly by anything that has to act on the
    /// current values before the debounce would have fired.
    func save() {
        guard settings != saved else { return }
        guard settings.isValid else {
            note("配置里有非法值，这一项没有写入", isError: true)
            return
        }
        do {
            try SettingsStore.save(settings)
            saved = settings
            // AppSettings are mtime-cached in the daemon, so this only makes the
            // pickup immediate rather than on the next event.
            try? DaemonClient.reload()
            note("已生效")
        } catch {
            note("写入失败：\(error.localizedDescription)", isError: true)
        }
    }

    func restoreDefaults() {
        settings = .defaults
        save()
    }

    // MARK: - daemon

    /// Off the main actor: the probe opens each VIA interface and waits for a
    /// reply, which is fast but not instant, and the window should not freeze
    /// while it happens.
    func rescan() {
        scanning = true
        Task.detached(priority: .userInitiated) {
            let report = DeviceScanner.scan()
            await MainActor.run {
                self.scanned = report?.devices
                self.scanning = false
            }
        }
    }

    func refreshStatus() {
        status = try? DaemonClient.status()
        installedHookEvents = Self.scanInstalledHooks()
    }

    func preview(_ key: String) {
        guard let spec = settings.states[key], spec.isValid else {
            note("这个状态的配置非法，无法预览", isError: true); return
        }
        // The debounce may not have fired yet, and a preview of anything other
        // than what is on screen would be a lie.
        save()
        do {
            try DaemonClient.preview(state: key, seconds: 3)
            note("预览 3 秒：\(AppSettings.displayName(key))")
        } catch {
            note(error.localizedDescription, isError: true)
        }
    }

    func resetLights() {
        do {
            try DaemonClient.reset()
            note("已清空任务状态并交还灯光")
        } catch {
            note(error.localizedDescription, isError: true)
        }
    }

    // MARK: - hooks

    /// The 7 events the installer wires up, in lifecycle order.
    static let hookEvents = ["UserPromptSubmit", "PermissionRequest", "PostToolUse",
                             "PostToolUseFailure", "Stop", "StopFailure", "SessionEnd"]

    /// Reads settings.json directly rather than asking the daemon: hooks are a
    /// property of the Claude Code config, not of the running service, and they
    /// need to be inspectable even when the daemon is down.
    static func scanInstalledHooks() -> Set<String> {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude/settings.json")
        guard let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let hooks = root["hooks"] as? [String: Any] else { return [] }
        var installed: Set<String> = []
        for (event, value) in hooks {
            guard let groups = value as? [[String: Any]] else { continue }
            for group in groups {
                guard let entries = group["hooks"] as? [[String: Any]] else { continue }
                if entries.contains(where: { ($0["command"] as? String)?.contains("ClaudeHalo65") == true }) {
                    installed.insert(event)
                }
            }
        }
        return installed
    }

    /// Shell out to the installed copy of install.py. Every button that changes
    /// something outside this app's own settings.json goes through here, so the
    /// installer stays the single place that knows how to edit another tool's
    /// config.
    func runInstaller(_ arguments: String...) {
        let script = SettingsStore.directory.appendingPathComponent("install.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            note("找不到 install.py，请先在项目里执行一次 install", isError: true)
            return
        }
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        task.arguments = [script.path] + arguments
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = pipe
        do {
            try task.run()
            task.waitUntilExit()
            let output = String(data: pipe.fileHandleForReading.readDataToEndOfFile(),
                                encoding: .utf8) ?? ""
            let lastLine = output.split(separator: "\n").last.map(String.init) ?? ""
            refreshStatus()
            note(task.terminationStatus == 0 ? lastLine : "失败：\(lastLine)",
                 isError: task.terminationStatus != 0)
        } catch {
            note("执行失败：\(error.localizedDescription)", isError: true)
        }
    }

    // MARK: -

    private var messageClearTask: Task<Void, Never>?

    /// Messages show as a transient toast over the detail pane, so each one
    /// clears itself; errors linger longer than confirmations.
    private func note(_ text: String, isError: Bool = false) {
        messageClearTask?.cancel()
        withAnimation(.easeInOut(duration: 0.15)) {
            message = text
            messageIsError = isError
        }
        let holdSeconds: UInt64 = isError ? 8 : 4
        messageClearTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: holdSeconds * 1_000_000_000)
            guard !Task.isCancelled else { return }
            withAnimation(.easeInOut(duration: 0.3)) { self?.message = nil }
        }
    }
}
