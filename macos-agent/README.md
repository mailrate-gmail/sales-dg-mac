# Sales-DG Mac Agent

macOS 本地无主窗口菜单栏工具，用系统 Speech Framework 做语音识别，通过现有后端接口写入会议文本。

## 功能

- 菜单栏红 / 黄 / 绿灯 + 状态文字显示状态、会议尾号和当前发言人；
- `Control + Option + 1-0` 切换不同发言人；
- `Control + Option + P` 暂停 / 继续；
- `Control + Option + Q` 退出；
- `Control + Option + E` 打开识别窗口；
- 识别后的 final 文本写入：`POST /api/meetings/{meeting_id}/transcripts`；
- 不上传原始音频；
- 不保存 LLM API Key。

## 构建

```bash
cd macos-agent
./build_app.sh
open SalesDGMacAgent.app
```

如果双击或 `open` 后没有反应，用调试方式启动并查看错误：

```bash
cd macos-agent
./run_debug.sh
```

运行日志会写入：

```text
~/Library/Logs/SalesDGMacAgent/agent.log
```

也可以实时查看：

```bash
tail -f ~/Library/Logs/SalesDGMacAgent/agent.log
```

首次启动会请求：

- 麦克风权限；
- 语音识别权限。

如果没有弹窗，可在 macOS 设置里手动打开：

```text
系统设置 → 隐私与安全性 → 麦克风
系统设置 → 隐私与安全性 → 语音识别
```

## 配置

首次启动会自动生成：

```text
~/.sales-dg-mac-agent.json
```

示例：

```json
{
  "backendBaseURL": "http://localhost:8000",
  "createMeetingOnLaunch": true,
  "customerName": "macOS 语音会议",
  "meetingGoal": "通过 macOS 系统语音识别记录会议内容。",
  "ingestToken": null,
  "localeIdentifier": "zh-CN",
  "meetingId": "20261120_103000",
  "speakers": [
    {
      "key": 1,
      "name": "张总",
      "participantId": "participant_ab12cd34",
      "role": "customer"
    },
    {
      "key": 2,
      "name": "我方李工",
      "participantId": "participant_cd34ef56",
      "role": "self"
    },
    {
      "key": 3,
      "name": "未知客户",
      "participantId": null,
      "role": "customer"
    }
  ]
}
```

说明：

- `createMeetingOnLaunch`：默认 `true`，启动时会优先复用上一次由 macOS Agent 创建且仍为 0 记录的会议；如果该会议已有记录，则自动调用 server 新建会议，并把返回的会议 ID 写回 `meetingId`。如需继续写入某个已有会议，可设为 `false` 后手动填写 `meetingId`；
- `customerName`：自动新建会议时发送给 server 的客户/会议名称；
- `meetingGoal`：自动新建会议时发送给 server 的会议目标；
- `meetingId`：当前会议 ID；自动新建成功后会更新为 server 返回的日期+时分秒 ID；
- `participantId`：建议填写后端已有参会人 ID；只有后端参会人配置为 `need_reply=true` 时才会触发 AI 回复；
- `ingestToken`：如果后端 `.env` 配置了 `TRANSCRIPT_INGEST_TOKEN`，这里填写相同 token；
- `localeIdentifier`：默认 `zh-CN`，可改为 `en-US` 等。

## 快捷键

| 快捷键 | 作用 |
| --- | --- |
| Control + Option + 1-0 | 切换当前发言人 |
| Control + Option + P | 暂停 / 继续识别 |
| Control + Option + Q | 退出 |
| Control + Option + E | 打开识别窗口 |

> F1-F12 容易被 macOS 系统功能键或其他 App 抢占，默认不再使用。

菜单栏状态示例：

```text
🟢 录音 112114 张总
```

含义：

- 绿灯：正在录音识别；
- `112114`：当前会议 ID 的时分秒尾号；
- `张总`：当前快捷键选择的发言人。

## 安全边界

- Mac Agent 只上传识别后的文本、会议 ID、发言人选择；
- 不上传原始音频；
- 不保存 LLM API Key；
- 后端可通过 `TRANSCRIPT_INGEST_TOKEN` 限制写入接口。

## 当前限制

- macOS 系统语音识别是否自动标点，取决于系统版本、语言和在线服务可用性；
- 系统 Speech 任务会话可能被 macOS 定期结束，Agent 会自动重启识别任务；
- `Control + Option + 数字/字母` 全局快捷键仍可能与少数 App 冲突；也可以从状态栏菜单手动选择发言人、暂停或退出。
