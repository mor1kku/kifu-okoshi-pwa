// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ProbeCore",
    platforms: [.macOS(.v13)],
    products: [.library(name: "ProbeCore", targets: ["ProbeCore"])],
    targets: [
        .target(name: "ProbeCore"),
        .testTarget(name: "ProbeCoreTests", dependencies: ["ProbeCore"])
    ]
)
