// SPDX-License-Identifier: MIT
import Foundation

public enum HaloMode: String, CaseIterable, Codable, Sendable {
    case release, solid, pulse, comet, strobe, fill

    /// Modes an active state may use. `release` hands the ring back, which only
    /// makes sense for idle.
    public static var activeCases: [HaloMode] { [.solid, .pulse, .comet, .strobe, .fill] }

    /// `param` means something different per mode, so the UI relabels its slider
    /// rather than showing a meaningless "param".
    public var paramLabel: String? {
        switch self {
        case .comet:  return "拖尾"
        case .strobe: return "占空比"
        default:      return nil
        }
    }

    public var paramRange: ClosedRange<Double> {
        switch self {
        case .comet:  return 2...50
        case .strobe: return 5...95
        default:      return 0...255
        }
    }

    public var displayName: String {
        switch self {
        case .release: return "交还固件"
        case .solid:   return "纯色"
        case .pulse:   return "整环脉冲"
        case .comet:   return "彗星绕圈"
        case .strobe:  return "快闪"
        case .fill:    return "扫圈填满"
        }
    }
}

/// The 50-LED ring around the base.
public struct HaloSpec: Codable, Equatable, Sendable {
    public var color: String
    public var brightness: Int
    public var mode: String
    public var speed: Int
    public var param: Int

    public var haloMode: HaloMode { HaloMode(rawValue: mode) ?? .solid }
    public var isValid: Bool {
        ColorHex.parse(color) != nil && (0...100).contains(brightness)
            && (0...255).contains(speed) && (0...255).contains(param)
            && HaloMode(rawValue: mode) != nil
    }
}

/// The typing-area RGB Matrix.
public struct MatrixSpec: Codable, Equatable, Sendable {
    public var color: String
    public var brightness: Int
    public var effect: Int
    public var speed: Int
    public var followColor: Bool
    public var restore: Bool

    enum CodingKeys: String, CodingKey {
        case color, brightness, effect, speed, restore
        case followColor = "follow_color"
    }

    /// Effect 0 blanks the LED driver and takes the ring down with it, so it is
    /// never offered. 42 is the top of the enum in the firmware built from
    /// firmware/halo-host-control.patch, taken from the compiler's expansion of
    /// `enum rgb_matrix_effects` and confirmed on hardware. NuPhy's factory
    /// build stopped at 45 -- a different effect set, so this is per-firmware.
    public static let effectRange = 1...42

    public struct Effect: Identifiable, Sendable {
        public let id: Int
        public let name: String
        /// Whether the effect paints with the configured hue. The cycle, rainbow
        /// and hue-* families animate hue themselves and discard it, which makes
        /// them pretty but useless as a status.
        public let honoursColor: Bool
    }

    /// Order and numbering come from the firmware's own enum, not from guesswork.
    public static let effects: [Effect] = [
        Effect(id: 1,  name: "纯色",                 honoursColor: true),
        Effect(id: 2,  name: "上下渐变",             honoursColor: true),
        Effect(id: 3,  name: "左右渐变",             honoursColor: true),
        Effect(id: 4,  name: "呼吸",                 honoursColor: true),
        Effect(id: 5,  name: "色带 · 饱和度",        honoursColor: true),
        Effect(id: 6,  name: "色带 · 亮度",          honoursColor: true),
        Effect(id: 7,  name: "风车色带 · 饱和度",    honoursColor: true),
        Effect(id: 8,  name: "风车色带 · 亮度",      honoursColor: true),
        Effect(id: 9,  name: "螺旋色带 · 饱和度",    honoursColor: true),
        Effect(id: 10, name: "螺旋色带 · 亮度",      honoursColor: true),
        Effect(id: 11, name: "整块彩虹循环",         honoursColor: false),
        Effect(id: 12, name: "彩虹左右循环",         honoursColor: false),
        Effect(id: 13, name: "彩虹上下循环",         honoursColor: false),
        Effect(id: 14, name: "彩虹人字形",           honoursColor: false),
        Effect(id: 15, name: "彩虹由外向内",         honoursColor: false),
        Effect(id: 16, name: "彩虹由外向内 · 双向",  honoursColor: false),
        Effect(id: 17, name: "彩虹风车",             honoursColor: false),
        Effect(id: 18, name: "彩虹螺旋",             honoursColor: false),
        Effect(id: 19, name: "双灯塔",               honoursColor: false),
        Effect(id: 20, name: "彩虹灯塔",             honoursColor: false),
        Effect(id: 21, name: "彩虹风车组",           honoursColor: false),
        Effect(id: 22, name: "雨滴",                 honoursColor: false),
        Effect(id: 23, name: "彩色雨滴",             honoursColor: false),
        Effect(id: 24, name: "色相呼吸",             honoursColor: false),
        Effect(id: 25, name: "色相钟摆",             honoursColor: false),
        Effect(id: 26, name: "色相波浪",             honoursColor: false),
        Effect(id: 27, name: "打字热力图",           honoursColor: false),
        Effect(id: 28, name: "数字雨",               honoursColor: false),
        Effect(id: 29, name: "按键点亮",             honoursColor: true),
        Effect(id: 30, name: "按键点亮 · 渐隐",      honoursColor: true),
        Effect(id: 31, name: "按键点亮 · 宽",        honoursColor: true),
        Effect(id: 32, name: "按键点亮 · 多点宽",    honoursColor: true),
        Effect(id: 33, name: "按键十字",             honoursColor: true),
        Effect(id: 34, name: "按键十字 · 多点",      honoursColor: true),
        Effect(id: 35, name: "按键行列",             honoursColor: true),
        Effect(id: 36, name: "按键行列 · 多点",      honoursColor: true),
        Effect(id: 37, name: "按键涟漪",             honoursColor: false),
        Effect(id: 38, name: "按键涟漪 · 多点",      honoursColor: false),
        Effect(id: 39, name: "按键涟漪 · 单色",      honoursColor: true),
        Effect(id: 40, name: "按键涟漪 · 单色多点",  honoursColor: true),
        Effect(id: 41, name: "游戏模式 (只亮 ESC/WASD/方向键)", honoursColor: true),
        Effect(id: 42, name: "定位模式 (只亮 F/J/↑)",           honoursColor: true),
    ]

    public static func effect(_ id: Int) -> Effect? { effects.first { $0.id == id } }

    public var isValid: Bool {
        ColorHex.parse(color) != nil && (0...100).contains(brightness)
            && Self.effectRange.contains(effect) && (0...255).contains(speed)
    }
}

public struct StateSpec: Codable, Equatable, Sendable {
    public var halo: HaloSpec
    public var matrix: MatrixSpec

    public var isValid: Bool { halo.isValid && (matrix.restore || matrix.isValid) }

    /// What the keys are actually painted with, once follow-colour is resolved.
    public var effectiveMatrixColor: String { matrix.followColor ? halo.color : matrix.color }
}

/// What turns the 语音输入 state on. Mirrors the "voice" block in settings.json,
/// which the daemon renders into voice.conf for halo65_voice to read.
public struct VoiceConfig: Codable, Equatable, Sendable {
    public var enabled: Bool
    /// hotkey | microphone | both
    public var trigger: String
    /// Virtual keycode, the same numbering CGEvent uses. 49 is Space.
    public var keycode: Int
    /// control / option / shift / command, named the way the event tap sees
    /// them. On a Mac with the modifiers swapped in System Settings the
    /// physical Command key reports control, and this is what must be written.
    public var modifiers: [String]
    /// hold | toggle
    public var mode: String
    public var tailSeconds: Double

    enum CodingKeys: String, CodingKey {
        case enabled, trigger, keycode, modifiers, mode
        case tailSeconds = "tail_seconds"
    }

    public static let triggers = ["hotkey", "microphone", "both"]
    public static let modes = ["hold", "toggle"]
    public static let modifierOrder = ["control", "option", "shift", "command"]
    public static let modifierSymbols = ["control": "⌃", "option": "⌥",
                                         "shift": "⇧", "command": "⌘"]

    /// Keys worth binding a dictation shortcut to. Anything else that ends up in
    /// the file still works; it just prints as its keycode.
    public static let keys: [(code: Int, name: String)] = [
        (49, "Space"), (36, "Return"), (48, "Tab"), (53, "Esc"),
        (122, "F1"), (120, "F2"), (99, "F3"), (118, "F4"), (96, "F5"), (97, "F6"),
        (98, "F7"), (100, "F8"), (101, "F9"), (109, "F10"), (103, "F11"), (111, "F12"),
    ]

    public static func keyName(_ code: Int) -> String {
        keys.first { $0.code == code }?.name ?? "键 \(code)"
    }

    public var shortcutDescription: String {
        let symbols = Self.modifierOrder
            .filter { modifiers.contains($0) }
            .compactMap { Self.modifierSymbols[$0] }
            .joined()
        return symbols + Self.keyName(keycode)
    }

    public var isValid: Bool {
        Self.triggers.contains(trigger) && Self.modes.contains(mode)
            && (0...127).contains(keycode) && (0...10).contains(tailSeconds)
            && modifiers.allSatisfy { Self.modifierOrder.contains($0) }
    }

    public static let defaults = VoiceConfig(enabled: false, trigger: "hotkey", keycode: 49,
                                             modifiers: ["control"], mode: "hold",
                                             tailSeconds: 1.2)
}

public struct Zones: Codable, Equatable, Sendable {
    public var halo: Bool
    public var matrix: Bool
}

/// Mirrors settings.json v3.
public struct AppSettings: Codable, Equatable, Sendable {
    public var version: Int
    public var zones: Zones
    public var completedHoldSeconds: Double
    public var failureHoldSeconds: Double
    public var staleActiveMinutes: Double
    public var staleSessionHours: Double
    public var states: [String: StateSpec]
    public var voice: VoiceConfig

    enum CodingKeys: String, CodingKey {
        case version, zones, states, voice
        case completedHoldSeconds = "completed_hold_seconds"
        case failureHoldSeconds = "failure_hold_seconds"
        case staleActiveMinutes = "stale_active_minutes"
        case staleSessionHours = "stale_session_hours"
    }

    /// Written by hand so that a settings.json from before the 语音输入 state
    /// still loads: the missing block falls back to the default instead of
    /// throwing and sending the whole window to defaults.
    public init(from decoder: Decoder) throws {
        let box = try decoder.container(keyedBy: CodingKeys.self)
        version = try box.decode(Int.self, forKey: .version)
        zones = try box.decode(Zones.self, forKey: .zones)
        completedHoldSeconds = try box.decode(Double.self, forKey: .completedHoldSeconds)
        failureHoldSeconds = try box.decode(Double.self, forKey: .failureHoldSeconds)
        staleActiveMinutes = try box.decode(Double.self, forKey: .staleActiveMinutes)
        staleSessionHours = try box.decode(Double.self, forKey: .staleSessionHours)
        states = try box.decode([String: StateSpec].self, forKey: .states)
        voice = try box.decodeIfPresent(VoiceConfig.self, forKey: .voice) ?? .defaults
        if states["voice"] == nil { states["voice"] = AppSettings.defaultVoiceState }
    }

    public init(version: Int, zones: Zones, completedHoldSeconds: Double,
                failureHoldSeconds: Double, staleActiveMinutes: Double,
                staleSessionHours: Double, states: [String: StateSpec], voice: VoiceConfig) {
        self.version = version
        self.zones = zones
        self.completedHoldSeconds = completedHoldSeconds
        self.failureHoldSeconds = failureHoldSeconds
        self.staleActiveMinutes = staleActiveMinutes
        self.staleSessionHours = staleSessionHours
        self.states = states
        self.voice = voice
    }

    /// idle last: it is the fallback everything returns to, not a status.
    public static let stateOrder = ["running", "permission", "failure", "completed",
                                    "voice", "idle"]

    public static func displayName(_ key: String) -> String {
        switch key {
        case "running":    return "执行中"
        case "permission": return "等待权限"
        case "failure":    return "工具失败"
        case "completed":  return "全部完成"
        case "voice":      return "语音输入"
        case "idle":       return "默认 / 空闲"
        default:           return key
        }
    }

    public static func explanation(_ key: String) -> String {
        switch key {
        case "running":
            return "提交问题后、Claude 正在工作时显示。这个状态你看得最多，选一个不刺眼的。"
        case "permission":
            return "Claude 在等你批准某个操作。这个要最抓眼 —— 整环同步明灭比局部运动更容易在余光里被注意到。"
        case "failure":
            return "工具执行失败时闪一下。Bash 退出码非 0 就算，所以它是瞬时提示，保持几秒后自动回到「执行中」。"
        case "completed":
            return "一轮回答结束、等你下一句时显示，保持一段时间后回到默认状态。"
        case "voice":
            return "你按下语音输入快捷键、或者麦克风被占用时显示。它压过上面四个状态：那几个是后台在做什么，这个是你此刻正在做什么。"
        case "idle":
            return "没有任何任务时的样子。默认是把两圈都交还固件 —— 也就是恢复你自己用 Fn 键设的灯效。也可以在这里指定一套固定的默认灯效。"
        default:
            return ""
        }
    }

    public static let defaults = AppSettings(
        version: 3,
        // The ring needs the patched firmware, so it is opt-in; the typing
        // area works on factory firmware and is what ships on. See "重新连接键盘"
        // in the advanced page.
        zones: Zones(halo: false, matrix: true),
        completedHoldSeconds: 10,
        failureHoldSeconds: 4,
        staleActiveMinutes: 30,
        staleSessionHours: 12,
        states: [
            "running": StateSpec(
                halo: HaloSpec(color: "#00A8FF", brightness: 100, mode: "comet", speed: 200, param: 12),
                matrix: MatrixSpec(color: "#00A8FF", brightness: 60, effect: 5, speed: 110,
                                   followColor: true, restore: false)),
            "permission": StateSpec(
                halo: HaloSpec(color: "#FFB000", brightness: 100, mode: "pulse", speed: 215, param: 0),
                matrix: MatrixSpec(color: "#FFB000", brightness: 75, effect: 5, speed: 230,
                                   followColor: true, restore: false)),
            "failure": StateSpec(
                halo: HaloSpec(color: "#FF2020", brightness: 100, mode: "strobe", speed: 230, param: 50),
                matrix: MatrixSpec(color: "#FF2020", brightness: 75, effect: 1, speed: 128,
                                   followColor: true, restore: false)),
            "completed": StateSpec(
                halo: HaloSpec(color: "#00E060", brightness: 100, mode: "fill", speed: 215, param: 0),
                matrix: MatrixSpec(color: "#00E060", brightness: 60, effect: 1, speed: 128,
                                   followColor: true, restore: false)),
            "voice": defaultVoiceState,
            "idle": StateSpec(
                halo: HaloSpec(color: "#000000", brightness: 100, mode: "release", speed: 0, param: 0),
                matrix: MatrixSpec(color: "#000000", brightness: 100, effect: 1, speed: 128,
                                   followColor: false, restore: true)),
        ],
        voice: .defaults
    )

    /// Steady purple: every agent state moves, so holding still is itself the
    /// distinction, and the colour is nowhere else in the set.
    public static let defaultVoiceState = StateSpec(
        halo: HaloSpec(color: "#A855F7", brightness: 100, mode: "solid", speed: 128, param: 0),
        matrix: MatrixSpec(color: "#A855F7", brightness: 75, effect: 1, speed: 128,
                           followColor: true, restore: false))

    public var isValid: Bool { states.values.allSatisfy(\.isValid) && voice.isValid }
}

public enum SettingsStore {
    public static var directory: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/ClaudeHalo65")
    }
    public static var url: URL { directory.appendingPathComponent("settings.json") }

    public static func load() throws -> AppSettings {
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(AppSettings.self, from: data)
    }

    /// Atomic so the daemon, which reloads on mtime, can never read a half file.
    public static func save(_ settings: AppSettings) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(settings)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let temporary = directory.appendingPathComponent("settings.json.app-tmp")
        try data.write(to: temporary, options: .atomic)
        _ = try FileManager.default.replaceItemAt(url, withItemAt: temporary)
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}

public enum ColorHex {
    /// Accepts "#RRGGBB" or "RRGGBB"; anything else is rejected so the UI can
    /// disable save rather than write a value the daemon would silently ignore.
    public static func parse(_ text: String) -> (r: Int, g: Int, b: Int)? {
        var hex = text.trimmingCharacters(in: .whitespaces)
        if hex.hasPrefix("#") { hex.removeFirst() }
        guard hex.count == 6, let value = Int(hex, radix: 16) else { return nil }
        return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
    }

    public static func string(r: Int, g: Int, b: Int) -> String {
        String(format: "#%02X%02X%02X", r, g, b)
    }
}

// MARK: - device discovery

/// One lighting subsystem a keyboard answered on, as reported by `via_scan`.
public struct ScannedChannel: Codable, Sendable {
    public let channel: String
    public let description: String
}

/// A QMK keyboard found on USB. Everything here comes from a read-only probe:
/// the identity from the USB descriptors, the lighting from asking VIA which
/// channels it implements.
public struct ScannedDevice: Codable, Identifiable, Sendable {
    public let vendorId: String
    public let productId: String
    public let manufacturer: String?
    public let product: String?
    public let reachable: Bool
    public let viaProtocol: Int?
    public let error: String?
    public let lighting: [String: ScannedChannel]?

    enum CodingKeys: String, CodingKey {
        case manufacturer, product, reachable, error, lighting
        case vendorId = "vendor_id"
        case productId = "product_id"
        case viaProtocol = "via_protocol"
    }

    public var id: String { "\(vendorId):\(productId)" }

    /// Vendor plus product, without repeating a vendor the product already names.
    public var displayName: String {
        let vendor = manufacturer ?? ""
        let name = product ?? "(no product string)"
        if vendor.isEmpty || name.lowercased().hasPrefix(vendor.lowercased()) { return name }
        return "\(vendor) \(name)"
    }

    public var hasMatrix: Bool { lighting?["rgb_matrix"] != nil }
    public var hasRing: Bool { lighting?["halo_ring"] != nil }
}

public struct ScanReport: Codable, Sendable {
    public let devices: [ScannedDevice]
}

public enum DeviceScanner {
    /// Runs the bundled `via_scan`. Returns nil when it is not installed yet;
    /// an empty device list is a real answer -- nothing is plugged in -- and is
    /// deliberately distinct from that.
    public static func scan() -> ScanReport? {
        let binary = SettingsStore.directory.appendingPathComponent("via_scan")
        guard FileManager.default.isExecutableFile(atPath: binary.path) else { return nil }
        let task = Process()
        task.executableURL = binary
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        // Read before waiting: the pipe buffer is small enough that a chatty
        // deep scan would deadlock the other way round.
        do { try task.run() } catch { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        task.waitUntilExit()
        return try? JSONDecoder().decode(ScanReport.self, from: data)
    }
}
