// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "SalesDGMacAgent",
    platforms: [
        .macOS(.v13)
    ],
    products: [
        .executable(name: "SalesDGMacAgent", targets: ["SalesDGMacAgent"])
    ],
    targets: [
        .executableTarget(
            name: "SalesDGMacAgent",
            linkerSettings: [
                .linkedFramework("AppKit"),
                .linkedFramework("AVFoundation"),
                .linkedFramework("Carbon"),
                .linkedFramework("Speech")
            ]
        )
    ]
)
