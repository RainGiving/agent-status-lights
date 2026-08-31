// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "ClaudeHalo65",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "ClaudeHalo65Core"),
        .executableTarget(name: "ClaudeHalo65", dependencies: ["ClaudeHalo65Core"]),
    ]
)
