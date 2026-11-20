#!/usr/bin/env bash
set -euo pipefail

APP_NAME="SalesDGMacAgent"
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$ROOT_DIR/.build/release"
APP_DIR="$ROOT_DIR/$APP_NAME.app"

cd "$ROOT_DIR"
swift build -c release

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
cp "$BUILD_DIR/$APP_NAME" "$APP_DIR/Contents/MacOS/$APP_NAME"

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>SalesDGMacAgent</string>
  <key>CFBundleIdentifier</key>
  <string>local.salesdg.macagent</string>
  <key>CFBundleName</key>
  <string>SalesDGMacAgent</string>
  <key>CFBundleDisplayName</key>
  <string>Sales-DG Mac Agent</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSMicrophoneUsageDescription</key>
  <string>Sales-DG Mac Agent 需要使用麦克风进行本地语音识别。</string>
  <key>NSSpeechRecognitionUsageDescription</key>
  <string>Sales-DG Mac Agent 需要使用 macOS 系统语音识别将会议语音转换为文字。</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>用于响应全局快捷键打开识别窗口。</string>
</dict>
</plist>
PLIST

echo "Built: $APP_DIR"
echo "Run: open '$APP_DIR'"
