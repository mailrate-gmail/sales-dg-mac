#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)/SalesDGMacAgent.app"
LOG_DIR="$HOME/Library/Logs/SalesDGMacAgent"
LOG_FILE="$LOG_DIR/agent.log"

if [[ ! -x "$APP_DIR/Contents/MacOS/SalesDGMacAgent" ]]; then
  echo "App not found. Build it first:"
  echo "  cd macos-agent && ./build_app.sh"
  exit 1
fi

mkdir -p "$LOG_DIR"
echo "Log file: $LOG_FILE"
echo "Starting: $APP_DIR"
echo "Press Ctrl+C here only stops this terminal command if the app exits; use F12 or menu to quit the app."
"$APP_DIR/Contents/MacOS/SalesDGMacAgent" 2>&1 | tee -a "$LOG_FILE"
