// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "ClaudeHalo75",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "ClaudeHalo75Core"),
        .executableTarget(name: "ClaudeHalo75", dependencies: ["ClaudeHalo75Core"]),
    ]
)
