// SPDX-License-Identifier: MIT
import AppKit
import SwiftUI

@main
struct ClaudeHalo75App: App {
    @StateObject private var model = AppModel()
    @Environment(\.openWindow) private var openWindow

    var body: some Scene {
        Window("Claude Halo75", id: "main") {
            RootView()
                .environmentObject(model)
                .onAppear { NSApp.activate(ignoringOtherApps: true) }
        }
        .defaultSize(width: 880, height: 600)

        // Quick access without leaving the window open all day.
        MenuBarExtra("Claude Halo75", systemImage: "circle.dashed") {
            Button("打开设置…") {
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }
            Divider()
            Button("交还灯光给固件") { model.resetLights() }
            Button("刷新状态") { model.refreshStatus() }
            Divider()
            Button("退出") { NSApplication.shared.terminate(nil) }
        }
    }
}
