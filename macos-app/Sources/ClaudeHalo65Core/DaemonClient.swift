// SPDX-License-Identifier: MIT
import Darwin
import Foundation

public struct DaemonStatus: Codable, Sendable {
    public let ok: Bool
    public let version: String?
    public let state: String?
    public let sessions: Int?
    public let sessionsByState: [String: Int]?
    public let previewing: Bool?
    public let haloSupported: Bool?
    public let error: String?

    enum CodingKeys: String, CodingKey {
        case ok, version, state, sessions, previewing, error
        case sessionsByState = "sessions_by_state"
        case haloSupported = "halo_supported"
    }
}

public struct DaemonReply: Codable, Sendable {
    public let ok: Bool
    public let error: String?
}

public enum DaemonError: LocalizedError {
    case notRunning
    case transport(String)
    case rejected(String)

    public var errorDescription: String? {
        switch self {
        case .notRunning:          return "后台服务未运行"
        case .transport(let detail): return "无法与后台服务通信：\(detail)"
        case .rejected(let detail):  return detail
        }
    }
}

/// Line-delimited JSON over the daemon's Unix socket.
///
/// Deliberately synchronous and short-lived: every call opens, writes, reads one
/// line and closes. The daemon serialises HID access behind its own lock, so a
/// long-lived connection would buy nothing and would need reconnect handling.
public enum DaemonClient {
    public static var socketPath: String {
        SettingsStore.directory.appendingPathComponent("status.sock").path
    }

    public static var isRunning: Bool {
        FileManager.default.fileExists(atPath: socketPath)
    }

    public static func send(_ request: [String: Any], timeout: TimeInterval = 6) throws -> Data {
        guard isRunning else { throw DaemonError.notRunning }

        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw DaemonError.transport("socket() failed") }
        defer { close(fd) }

        var tv = timeval(tv_sec: Int(timeout), tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(socketPath.utf8)
        guard pathBytes.count < MemoryLayout.size(ofValue: address.sun_path) else {
            throw DaemonError.transport("socket path too long")
        }
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.copyBytes(from: pathBytes)
        }

        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { throw DaemonError.notRunning }

        var payload = try JSONSerialization.data(withJSONObject: request)
        payload.append(0x0A)
        try payload.withUnsafeBytes { buffer in
            var sent = 0
            while sent < buffer.count {
                let n = write(fd, buffer.baseAddress!.advanced(by: sent), buffer.count - sent)
                guard n > 0 else { throw DaemonError.transport("write failed") }
                sent += n
            }
        }

        var response = Data()
        var chunk = [UInt8](repeating: 0, count: 4096)
        while true {
            let n = read(fd, &chunk, chunk.count)
            if n <= 0 { break }
            response.append(contentsOf: chunk[0..<n])
            if chunk[0..<n].contains(0x0A) { break }
        }
        guard !response.isEmpty else { throw DaemonError.transport("empty reply") }
        return response
    }

    public static func status() throws -> DaemonStatus {
        let data = try send(["command": "status"])
        return try JSONDecoder().decode(DaemonStatus.self, from: data)
    }

    /// Previews a *saved* state by name: the daemon owns the composition of the
    /// two zones, so the app must not try to reproduce it over the wire.
    public static func preview(state: String, seconds: Double = 3) throws {
        let data = try send([
            "command": "preview", "state": state, "seconds": seconds,
        ], timeout: 10)
        let reply = try JSONDecoder().decode(DaemonReply.self, from: data)
        if !reply.ok { throw DaemonError.rejected(reply.error ?? "预览被拒绝") }
    }

    public static func reload() throws {
        _ = try send(["command": "reload"], timeout: 10)
    }

    public static func reset() throws {
        _ = try send(["command": "reset"], timeout: 10)
    }
}
