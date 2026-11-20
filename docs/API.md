# API 接口文档

本文档描述 AI 会谈辅助系统后端主要业务 API。默认服务地址：

```text
http://localhost:8000
```

所有 JSON 接口默认使用：

```http
Content-Type: application/json
```

音频上传接口使用：

```http
Content-Type: multipart/form-data
```

## 1. 通用约定

### 1.1 角色枚举

| 值 | 含义 |
| --- | --- |
| `self` | 我方 |
| `customer` | 客户 |
| `other` | 其他 |
| `unknown` | 未知 |

### 1.2 错误格式

FastAPI 默认错误格式：

```json
{
  "detail": "错误说明"
}
```

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| `400` | 请求内容不满足业务要求，例如无有效语音 |
| `404` | 会议、消息、参会人或识别个体不存在 |
| `422` | 请求参数格式错误 |
| `500` | 服务端异常或外部模型服务异常 |

## 2. 健康检查

### GET `/api/health`

用于检查后端服务是否可用。

**响应示例**

```json
{
  "status": "ok"
}
```

## 3. 会议业务 API

推荐新业务优先使用 `/api/meetings` 系列接口。旧的 `/api/sessions` 接口仍保留兼容。

### 3.1 新建会议

### POST `/api/meetings`

创建一场会议。前端只需要传客户名称和会晤目标，开始时间由后端自动生成。会议 ID 使用本地时间的日期+时分秒格式：`yyyyMMdd_HHmmss`，例如 `20261118_103000`。

> 如果同一秒内重复创建会议导致 ID 冲突，接口会返回 `409`，稍后重试即可。

**请求体**

```json
{
  "customer_name": "上海某某科技有限公司",
  "goal": "了解客户当前业务痛点，并推动下一次需求调研。"
}
```

**响应示例**

```json
{
  "id": "20261118_103000",
  "title": "上海某某科技有限公司会谈",
  "goal": "了解客户当前业务痛点，并推动下一次需求调研。",
  "started_at": "2026-11-18T10:30:00",
  "ended_at": null,
  "messages": [],
  "summary": null
}
```

### 3.2 查询会议列表

### GET `/api/meetings`

查询本地历史会议列表。

**响应示例**

```json
[
  {
    "id": "20261118_103000",
    "title": "上海某某科技有限公司会谈",
    "goal": "了解客户当前业务痛点，并推动下一次需求调研。",
    "started_at": "2026-11-18T10:30:00",
    "ended_at": null,
    "message_count": 12,
    "has_summary": true
  }
]
```

### 3.3 查询会议详情

### GET `/api/meetings/{meeting_id}`

获取指定会议的完整信息，包括实时会话消息和会议总结。

**路径参数**

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `meeting_id` | string | 会议 ID |

**响应示例**

```json
{
  "id": "20261118_103000",
  "title": "上海某某科技有限公司会谈",
  "goal": "了解客户当前业务痛点，并推动下一次需求调研。",
  "started_at": "2026-11-18T10:30:00",
  "ended_at": null,
  "messages": [
    {
      "id": "message_1234567890",
      "session_id": "20261118_103000",
      "participant_id": null,
      "speaker_identity_id": "voice_ab12cd34",
      "speaker_name": "张经理",
      "role": "customer",
      "text": "我们现在主要问题是线索跟进不及时。",
      "audio_file": "/app/data/audio/session_.../message_1234567890.wav",
      "speaker_score": 0.63,
      "need_reply": true,
      "ai_suggestion": null,
      "created_at": "2026-11-18T10:31:05",
      "corrected": true,
      "utterance_labels": ["question", "pain_point"],
      "should_reply": true,
      "analysis_reason": "识别为问题或明确请求，触发 AI 建议回复"
    }
  ],
  "summary": null
}
```

> **AI 回复过滤**：`need_reply` 表示发言人被指定为“需要答复”，`should_reply` 表示本地规则判定这条发言是问题/明确请求。只有两者都为 `true` 时才会调用远程 LLM 生成 `ai_suggestion`。说明、示例、解释等内容（`context_note`）会有 `need_reply=true` 但 `should_reply=false`，`ai_suggestion` 为 `null`，不会出现在 AI Q&A 区，但其文本会写入 Agent 记忆的 `context_notes` / `key_facts`，供后续回复与总结参考。`utterance_labels` 与 `analysis_reason` 用于追溯过滤依据。

### 3.4 删除会议

### DELETE `/api/meetings/{meeting_id}`

删除指定会议及相关本地文件，包括会议记录、Agent 记忆、音频目录、未知说话人聚类目录和导出 Markdown 文件。

**响应示例**

```json
{
  "deleted": true,
  "id": "20261118_103000"
}
```

> 兼容接口：`DELETE /api/sessions/{session_id}`。

### 3.5 上传会议音频片段并处理

### POST `/api/meetings/{meeting_id}/audio`

上传一段前端切分后的音频片段。后端会按当前 `asr_provider` 分派到 FunASR streaming 或 faster-whisper；对于较长 final 音频，后端还会按静音/最大时长做二次切分，避免一条消息混入过长多说话人内容。后端会完成：

1. 音频转 WAV；
2. ASR 转写与 final 标点恢复；
3. SpeechBrain ECAPA 声纹识别/未知说话人聚类；
4. 写入会议消息；
5. 如该说话人需要 AI 回复，则生成建议回复。

**请求类型**

```http
multipart/form-data
```

**表单字段**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | file | 是 | 音频片段，推荐 webm |

**curl 示例**

```bash
curl -X POST \
  -F "file=@speech.webm" \
  http://localhost:8000/api/meetings/20261118_103000/audio
```

**响应示例**

```json
[
  {
    "id": "message_1234567890_1",
    "session_id": "20261118_103000",
    "participant_id": null,
    "speaker_identity_id": "voice_ab12cd34",
    "speaker_name": "未知说话人",
    "role": "unknown",
    "text": "我们现在主要问题是线索跟进不及时。",
    "start_time": 0.0,
    "end_time": 3.4,
    "audio_file": "/app/data/audio/session_.../message_1234567890.wav",
    "speaker_score": 0.63,
    "need_reply": false,
    "ai_suggestion": null,
    "created_at": "2026-11-18T10:31:05",
    "corrected": false
  }
]
```

### 3.6 写入外部系统 ASR 文本

### POST `/api/meetings/{meeting_id}/transcripts`

用于 macOS Agent 等本地工具使用系统 ASR 识别文本并由用户手动选择发言人，然后只把文本写入后端。该接口**不上传音频、不做声纹识别**，用于降低本地采集端隐私风险和实现复杂度。

如果服务端配置了：

```env
TRANSCRIPT_INGEST_TOKEN=your-token
```

调用方必须带：

```http
Authorization: Bearer your-token
```

**请求体**

```json
{
  "text": "客户刚才问我们是否支持私有化部署。",
  "participant_id": "participant_ab12cd34",
  "speaker_name": "张总",
  "role": "customer",
  "need_reply": true,
  "source": "macos_speech_asr",
  "recognized_at": "2026-11-20T10:30:00"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `text` | string | 是 | 外部系统 ASR 识别后的 final 文本，通常已带标点 |
| `participant_id` | string | 否 | 已登记参会人 ID；传入后以后端参会人配置为准 |
| `speaker_name` | string | 否 | 未传 `participant_id` 时使用的显示名称 |
| `role` | string | 否 | `self` / `customer` / `other` / `unknown`，默认 `unknown` |
| `need_reply` | boolean | 否 | 兼容字段；为安全起见，只有传入有效 `participant_id` 时才以后端参会人配置决定是否触发 AI 回复 |
| `source` | string | 否 | 来源标记，默认 `external_system_asr`；macOS Agent 使用 `macos_speech_asr` |
| `recognized_at` | string | 否 | 采集端识别时间；不传则使用服务端接收时间 |

> 安全约束：只有 `participant_id` 有效，且该参会人在后端配置为 `need_reply=true` 时，才可能自动生成 AI 回复。未登记的手动发言人只写入会议上下文，不触发 AI 自动回复。

**curl 示例**

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-token" \
  -d '{"text":"客户刚才问我们是否支持私有化部署。","participant_id":"participant_ab12cd34","source":"macos_speech_asr"}' \
  http://localhost:8000/api/meetings/20261118_103000/transcripts
```

**响应**

返回写入后的 `Message`。后端会照常更新会议记忆，并在满足 `need_reply=true` 且本地规则判断为问题/明确请求时生成 AI 建议回复。

### 3.7 绑定识别个体 ID 与参会人员名称

### PUT `/api/meetings/{meeting_id}/speakers/{speaker_identity_id}`

将会议室里动态识别到的某个个体 ID 与人工输入的参会人员名称关联。该接口会批量更新当前会议中相同 `speaker_identity_id` 的消息。

> 注意：只有已经发言并被识别到的个体才会有 `speaker_identity_id`。未发言人员不会出现在会议室关联表中。

**路径参数**

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `meeting_id` | string | 会议 ID |
| `speaker_identity_id` | string | 识别个体 ID，例如 `voice_ab12cd34` 或已登记声纹的 `participant_xxx` |

**请求体**

```json
{
  "speaker_name": "张经理",
  "role": "customer",
  "need_reply": true
}
```

**响应示例**

```json
{
  "speaker_identity_id": "voice_ab12cd34",
  "speaker_name": "张经理",
  "updated_count": 3
}
```

### 3.8 手动修正单条消息说话人

### PUT `/api/sessions/{session_id}/messages/{message_id}/speaker`

用于精确修正单条消息。该接口属于兼容 API，目前前端仍可用于从已登记声纹参会人中选择修正。

**请求体：绑定到已登记参会人**

```json
{
  "participant_id": "participant_ab12cd34"
}
```

**请求体：只修正名称和角色**

```json
{
  "speaker_name": "张经理",
  "role": "customer"
}
```

**响应**

返回修正后的 `Message`。

### 3.9 结束会议

### POST `/api/meetings/{meeting_id}/end`

设置会议结束时间。结束时间由后端自动生成；重复调用不会覆盖已有结束时间。

**响应**

返回更新后的会议详情。

### 3.10 生成会议总结

### POST `/api/meetings/{meeting_id}/summary`

人工触发生成会议总结。系统不会自动生成总结。

**响应示例**

```json
{
  "summary": "1. 会谈概况\n...\n2. 客户关键诉求/痛点\n..."
}
```

### 3.11 导出会议 Markdown

### POST `/api/meetings/{meeting_id}/export`

导出会议记录到本地 Markdown 文件。

**响应示例**

```json
{
  "file": "/app/data/exports/20261118_103000.md"
}
```

## 4. Agent 上下文与记忆 API

### 4.1 查询会议 Agent 记忆

### GET `/api/meetings/{meeting_id}/agent/memory`

返回当前会议的 Agent 记忆，包括短期上下文、本地归纳的背景说明与关键事实、客户痛点、风险、承诺、待办、问题和决策日志。

**响应示例**

```json
{
  "meeting_id": "20261118_103000",
  "goal": "了解客户当前业务痛点，并推动下一次需求调研。",
  "short_term_context": ["张经理: 我们线索跟进不及时。"],
  "context_notes": ["张经理: 比如我们现在线索是通过邮件同步的。"],
  "key_facts": ["张经理: 我们目前已经接入了三家客户。"],
  "pain_points": ["张经理: 我们线索跟进不及时。"],
  "risks": [],
  "commitments": [],
  "todos": [],
  "questions": [],
  "decisions": [
    {
      "id": "decision_ab12cd34ef",
      "type": "extract_pain_point",
      "reason": "发言中包含痛点或业务问题",
      "content": "我们线索跟进不及时。",
      "message_id": "message_1234567890",
      "speaker_name": "张经理",
      "created_at": "2026-11-18T10:31:05"
    }
  ],
  "updated_at": "2026-11-18T10:31:05"
}
```

### 4.2 重新运行会议 Agent

### POST `/api/meetings/{meeting_id}/agent/run`

从当前会议全部消息重新构建 Agent 记忆。适用于手动修正参会人名称后刷新 Agent 工作台。

**响应示例**

```json
{
  "memory": {
    "meeting_id": "20261118_103000",
    "goal": "了解客户当前业务痛点，并推动下一次需求调研。",
    "short_term_context": [],
    "pain_points": [],
    "risks": [],
    "commitments": [],
    "todos": [],
    "questions": [],
    "decisions": [],
    "updated_at": "2026-11-18T10:40:00"
  },
  "actions": []
}
```

## 5. 参会人和声纹 API

参会人 API 用于维护已知参会人和声纹样本。会议室中的“识别个体 ID 关联”是动态的，不要求提前创建参会人。

### 5.1 查询参会人列表

### GET `/api/participants`

**响应示例**

```json
[
  {
    "id": "participant_ab12cd34",
    "name": "李销售",
    "role": "self",
    "need_reply": false,
    "display_color": "yellow",
    "voiceprint_file": "/app/data/participants/participant_ab12cd34.embedding.npy",
    "sample_audio_file": "/app/data/participants/participant_ab12cd34_sample.wav",
    "remark": ""
  }
]
```

### 5.2 创建参会人

### POST `/api/participants`

**请求体**

```json
{
  "name": "李销售",
  "role": "self",
  "need_reply": false,
  "remark": "我方销售负责人"
}
```

### 5.3 更新参会人

### PUT `/api/participants/{participant_id}`

**请求体**

```json
{
  "name": "李销售",
  "role": "self",
  "need_reply": false,
  "remark": "更新后的备注"
}
```

字段均可选。

### 5.4 上传声纹样本

### POST `/api/participants/{participant_id}/voiceprint`

上传参会人的声纹样本。后端会转 WAV 并生成声纹 embedding。

**请求类型**

```http
multipart/form-data
```

**表单字段**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | file | 是 | 声纹样本音频，推荐 10 秒左右 webm |

**curl 示例**

```bash
curl -X POST \
  -F "file=@voiceprint.webm" \
  http://localhost:8000/api/participants/participant_ab12cd34/voiceprint
```

## 6. AI API

### 6.1 独立生成建议回复

### POST `/api/ai/suggest-reply`

不依赖会议消息存储，直接根据传入文本生成建议回复。

**请求体**

```json
{
  "meeting_goal": "推动下一次需求调研",
  "conversation_history": "张经理：我们线索跟进不及时。",
  "latest_customer_message": "这个问题影响了销售转化。"
}
```

**响应示例**

```json
{
  "suggestion": "我理解这个问题会直接影响销售转化。我们可以先梳理线索流转节点，再确认哪些环节最容易延迟。"
}
```

## 7. 系统配置 API

### 7.1 查询模型配置

### GET `/api/config`

**响应示例**

```json
{
  "llm_provider": "deepseek",
  "llm_model": "deepseek-chat",
  "llm_base_url": "https://api.deepseek.com",
  "llm_api_key": "",
  "llm_api_type": "chat_completions",
  "llm_timeout_seconds": 30,
  "model_availability": {
    "deepseek": true,
    "gpt": false,
    "qwen": false,
    "grok": false
  },
  "custom_models": [
    {
      "id": "oci-gpt55",
      "name": "OCI GPT-5.5",
      "model": "openai.gpt-5.5",
      "base_url": "https://your-oci-endpoint.example.com",
      "api_type": "responses",
      "description": "用户自定义 OCI 模型"
    }
  ],
  "asr_provider": "funasr_streaming",
  "whisper_model": "small",
  "whisper_device": "cpu",
  "whisper_compute_type": "int8",
  "bilingual_asr_enabled": false,
  "funasr_model": "paraformer-zh-streaming",
  "funasr_device": "cpu",
  "oci_speech_region": "us-ashburn-1",
  "oci_speech_compartment_id": "ocid1.compartment.oc1...",
  "oci_speech_language": "zh-CN",
  "oci_speech_profile": "DEFAULT",
  "oci_speech_config_file": "/root/.oci/config",
  "oci_speech_realtime_endpoint": "",
  "speaker_threshold": 0.3,
  "speaker_cluster_threshold": 0.45,
  "speaker_diarization_enabled": false,
  "energy_threshold": 0.008,
  "silence_duration_ms": 800,
  "min_speech_duration_ms": 1000,
  "punctuation_provider": "funasr_punc",
  "punctuation_model": "ct-punc",
  "punctuation_device": "cpu"
}
```

> **API Key 安全**：`GET /api/config` 对已保存的 `llm_api_key` 仅返回掩码 `__MASKED__`；`PUT /api/config` 收到该掩码时保留原密钥，输入新值则覆盖，清空则删除。`POST /api/config/test` 同样会先将掩码还原为已保存密钥再测试。

> **ASR 后端选择**：`asr_provider` 控制短音频上传接口（`POST /api/meetings/{id}/audio` 与 `POST /api/audio/process`）使用的引擎：`funasr_streaming`（默认）用本地 FunASR 流式模型整段识别；`faster_whisper` 用本地 faster-whisper 识别并返回带时间戳分段；`oci_speech` 尚未接入，返回 `501`。会议室实时流式 WebSocket 识别固定使用 FunASR，不受该字段影响。

> **双语识别增强**：`bilingual_asr_enabled` 默认 `false`。开启后，会议 final 音频和 `funasr_streaming` 短音频路径会优先使用 faster-whisper 自动语言识别来增强中英文混合转写；增强失败时回退 FunASR streaming 结果。该选项会加载/使用 faster-whisper 模型，网络或缓存不稳定时建议保持关闭。

> **标点 Provider**：`punctuation_provider` 支持 `funasr_punc` / `asr` / `rule` / `off`。实时 FunASR streaming 的原始 final 通常没有标点，因此默认使用 `funasr_punc`（HuggingFace `funasr/ct-punc`）为 final 文本补标点；前端不再对 final 文本二次补标点。`rule` 仅作为固定保守规则 fallback，不再提供敏感度参数。

> **说话人阈值**：`speaker_threshold` 用于匹配已录入声纹的参会人；`speaker_cluster_threshold` 用于将未知说话人的多段语音合并成同一个 `voice_xxx`。同一个人被拆成多个 ID 时可降低 `speaker_cluster_threshold`（如 `0.40` / `0.35`）；不同人被合并时可调高。`speaker_diarization_enabled` 控制可选 FunASR `cam++` / `campplus` 说话人分离，默认关闭；该链路已改用 HuggingFace 模型源（`funasr/paraformer-zh`、`funasr/fsmn-vad`、`funasr/campplus`），首次触发时会惰性下载。

### 7.2 更新模型配置

### PUT `/api/config`

**请求体**

传完整配置对象，字段同上。

**响应**

返回保存后的完整配置。配置会持久化到：

```text
data/model_config.json
```

### 7.3 测试当前模型连接

### POST `/api/config/test`

测试当前或传入的 OpenAI Compatible LLM 配置是否可用。该接口会根据 `llm_api_type` 发送一个极小的测试请求。

端点 URL 由 `llm_base_url` 与 `llm_api_type` 推导，并会自动避免重复拼接：

- `responses`：调用 `/v1/responses`；`chat_completions`：调用 `/v1/chat/completions`；
- 若 `llm_base_url` 已包含完整端点（如 OCI 的 `.../20231130/actions/v1/responses`），则直接沿用，不会再次追加；
- 若 `llm_base_url` 以 `/v1` 结尾，则只追加 `/responses` 或 `/chat/completions`；
- 否则按 `{llm_base_url}/v1/...` 拼接（如 DeepSeek、OpenAI 官方域名）。

该机制适用于 GPT-5 / xAI / 部分 OCI OpenAI Compatible 服务等 Responses API 模式。

**请求体**

可不传；不传时使用当前保存的配置。也可以传完整 `ModelConfig` 用于测试尚未保存的配置。

**响应示例**

```json
{
  "ok": true,
  "provider": "qwen",
  "model": "qwen-plus",
  "message": "OK",
  "latency_ms": 823
}
```

失败示例：

```json
{
  "ok": false,
  "provider": "gpt",
  "model": "gpt-4o-mini",
  "message": "未配置 API Key。",
  "latency_ms": 0
}
```

## 8. 兼容接口

以下接口保留以兼容早期前端或外部调用方：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/sessions` | 创建会话，等价于早期会议创建 |
| `GET` | `/api/sessions` | 查询会话列表 |
| `GET` | `/api/sessions/{session_id}` | 查询会话详情 |
| `POST` | `/api/audio/process` | 上传音频片段；表单需包含 `session_id` 和 `file` |
| `POST` | `/api/sessions/{session_id}/summary` | 生成会后总结 |
| `POST` | `/api/sessions/{session_id}/export` | 导出会话 Markdown |
| `PUT` | `/api/sessions/{session_id}/messages/{message_id}/speaker` | 修正单条消息说话人 |

## 9. 典型业务调用流程

### 9.1 新会议实时辅助流程

1. `POST /api/meetings` 创建会议；
2. 前端浏览器采集音频并按能量切分；
3. 每个音频片段调用 `POST /api/meetings/{meeting_id}/audio`；
4. 前端展示返回的 `speaker_identity_id`、转写文本和 AI 建议；
5. 用户输入参会人名称后调用 `PUT /api/meetings/{meeting_id}/speakers/{speaker_identity_id}`；
6. 会议结束后人工点击，调用 `POST /api/meetings/{meeting_id}/summary`；
7. 需要保存时调用 `POST /api/meetings/{meeting_id}/export`。

### 9.2 已知声纹参会人流程

1. `POST /api/participants` 创建参会人；
2. `POST /api/participants/{participant_id}/voiceprint` 上传声纹样本；
3. 会议音频处理时，后端会尝试匹配该参会人的声纹；
4. 如果匹配成功，消息中的 `participant_id` 与 `speaker_identity_id` 会使用该参会人 ID。
