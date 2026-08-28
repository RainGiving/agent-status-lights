// SPDX-License-Identifier: MIT
import ClaudeHalo75Core
import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    @Published var settings: AppSettings = .defaults
    @Published var saved: AppSettings = .defaults
    @Published var status: DaemonStatus?
    @Published var message: String?
    @Published var messageIsError = false
    @Published var hooksInstalled: Int = 0

    private var pollTimer: Timer?

    var isDirty: Bool { settings != saved }
    var canSave: Bool { settings.isValid && isDirty }
    var daemonRunning: Bool { status?.ok == true }
    var haloSupported: Bool { status?.haloSupported == true }
    var sessionsByState: [String: Int] { status?.sessionsByState ?? [:] }

    init() {
        reload()
        refreshStatus()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refreshStatus() }
        }
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

    func save() {
        guard settings.isValid else {
            note("配置里有非法值，未保存", isError: true)
            return
        }
        do {
            try SettingsStore.save(settings)
            saved = settings
            // AppSettings are mtime-cached in the daemon, so this only makes the
            // pickup immediate rather than on the next event.
            try? DaemonClient.reload()
            note("已保存并应用")
        } catch {
            note("保存失败：\(error.localizedDescription)", isError: true)
        }
    }

    func restoreDefaults() {
        settings = .defaults
        note("已恢复默认值，点保存后生效")
    }

    // MARK: - daemon

    func refreshStatus() {
        status = try? DaemonClient.status()
        hooksInstalled = Self.countInstalledHooks()
    }

    func preview(_ key: String) {
        guard let spec = settings.states[key], spec.isValid else {
            note("这个状态的配置非法，无法预览", isError: true); return
        }
        if isDirty {
            note("预览的是已保存的配置。先点「保存并应用」再预览。", isError: true)
        }
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

    /// Reads settings.json directly rather than asking the daemon: hooks are a
    /// property of the Claude Code config, not of the running service, and they
    /// need to be inspectable even when the daemon is down.
    static func countInstalledHooks() -> Int {
        let url = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".claude/settings.json")
        guard let data = try? Data(contentsOf: url),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let hooks = root["hooks"] as? [String: Any] else { return 0 }
        var count = 0
        for (_, value) in hooks {
            guard let groups = value as? [[String: Any]] else { continue }
            for group in groups {
                guard let entries = group["hooks"] as? [[String: Any]] else { continue }
                if entries.contains(where: { ($0["command"] as? String)?.contains("ClaudeHalo75") == true }) {
                    count += 1
                }
            }
        }
        return count
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

    private func note(_ text: String, isError: Bool = false) {
        message = text
        messageIsError = isError
    }
}
