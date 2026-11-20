from datetime import datetime
from pathlib import Path
from uuid import uuid4
import asyncio
import logging
import secrets
import wave
import numpy as np

from fastapi import Body, FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models import (
    AgentMemory,
    AgentRunResponse,
    ExternalTranscriptCreate,
    ExportResponse,
    ConnectionTestResponse,
    MeetingCreate,
    Message,
    ModelConfig,
    Participant,
    ParticipantCreate,
    ParticipantUpdate,
    Session,
    SessionCreate,
    SessionSummary,
    SpeakerBindRequest,
    SpeakerBindResponse,
    SpeakerCorrectionRequest,
    SuggestReplyRequest,
    SuggestReplyResponse,
    SummaryResponse,
)
from app.services.asr_dispatch import transcribe_audio_file
from app.services.audio import audio_service
from app.services.config_view import mask_config, unmask_api_key
from app.services.context_manager import context_manager
from app.services.funasr_streaming import funasr_streaming_service
from app.services.llm_client import llm_client
from app.services.punctuation_model import get_punctuation_model, restore_final_punctuation
from app.services.speaker_diarization import get_speaker_diarization_model, transcribe_speaker_segments
from app.services.runtime_config import runtime_config
from app.services.speaker import speaker_service
from app.services.storage import storage
from app.services.text_merge import merge_stream_text

app = FastAPI(title="AI Meeting Assistant")
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_participants() -> list[Participant]:
    return [Participant(**item) for item in storage.read_json(storage.participants_file, [])]


def save_participants(participants: list[Participant]) -> None:
    storage.write_json(storage.participants_file, [p.model_dump() for p in participants])


def verify_transcript_ingest_token(authorization: str | None) -> None:
    if not settings.transcript_ingest_token:
        return
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="缺少外部转写写入授权。")
    token = authorization[len(prefix) :]
    if not secrets.compare_digest(token, settings.transcript_ingest_token):
        raise HTTPException(status_code=401, detail="外部转写写入授权无效。")


def load_session(session_id: str) -> Session:
    path = storage.session_file(session_id)
    data = storage.read_json(path)
    if not data:
        raise HTTPException(status_code=404, detail="会话不存在")
    return Session(**data)


def save_session(session: Session) -> None:
    storage.write_json(storage.session_file(session.id), session.model_dump())


def create_session_from_title_goal(title: str, goal: str) -> Session:
    session = Session(
        id=f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}",
        title=title,
        goal=goal,
        started_at=now_iso(),
    )
    save_session(session)
    return session


def participant_to_message_fields(participant: Participant) -> dict:
    return {
        "participant_id": participant.id,
        "speaker_name": participant.name,
        "role": participant.role,
        "need_reply": participant.need_reply,
    }


def write_pcm16_wav(path: Path, pcm_bytes: bytes, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm_bytes)


def reusable_unknown_speaker_id(session: Session) -> str:
    for message in reversed(session.messages):
        if message.participant_id is None and message.speaker_identity_id:
            return message.speaker_identity_id
    return f"voice_{uuid4().hex[:8]}"


# ECAPA 在过短音频上的 embedding 不可靠；短于该时长时跳过声纹识别/聚类，
# 避免误匹配已知参会人或把同一人拆成多个 ID。
MIN_SPEAKER_EMBEDDING_SECONDS = 3.0
PCM_SAMPLE_RATE = 16000
MAX_SINGLE_SPEAKER_SEGMENT_SECONDS = 10.0
MIN_SPLIT_SEGMENT_SECONDS = 1.8
SILENCE_SPLIT_SECONDS = 0.35


def wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav:
            rate = wav.getframerate()
            return wav.getnframes() / rate if rate else 0.0
    except Exception:
        return 0.0


def read_wav_pcm16(path: Path) -> bytes:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != PCM_SAMPLE_RATE:
            raise ValueError("仅支持 16kHz mono PCM16 wav")
        return wav.readframes(wav.getnframes())


def slice_pcm16(pcm_bytes: bytes, start_seconds: float, end_seconds: float) -> bytes:
    total_samples = len(pcm_bytes) // 2
    start_sample = max(0, min(total_samples, int(start_seconds * PCM_SAMPLE_RATE)))
    end_sample = max(start_sample, min(total_samples, int(end_seconds * PCM_SAMPLE_RATE)))
    return pcm_bytes[start_sample * 2:end_sample * 2]


def split_pcm16_for_speaker_turns(pcm_bytes: bytes) -> list[bytes]:
    """把过长语音段按停顿/最大时长切成更适合声纹识别的小段。

    这不是完整 speaker diarization；它解决的是浏览器端 VAD 没切开，导致一条 30~60 秒
    final 音频中混入多位说话人的常见问题。若多人无停顿重叠说话，仍需要专门 diarization 模型。
    """
    total_samples = len(pcm_bytes) // 2
    if total_samples <= int(MAX_SINGLE_SPEAKER_SEGMENT_SECONDS * PCM_SAMPLE_RATE):
        return [pcm_bytes]

    audio = np.frombuffer(pcm_bytes, dtype=np.int16)
    if audio.size == 0:
        return []

    frame_samples = int(0.02 * PCM_SAMPLE_RATE)
    silence_frames_needed = max(1, int(SILENCE_SPLIT_SECONDS / 0.02))
    min_samples = int(MIN_SPLIT_SEGMENT_SECONDS * PCM_SAMPLE_RATE)
    max_samples = int(MAX_SINGLE_SPEAKER_SEGMENT_SECONDS * PCM_SAMPLE_RATE)
    # 使用比前端触发阈值略低的能量阈值找“停顿”，避免轻微底噪阻止切分。
    energy_threshold = max(0.0025, min(runtime_config.get().energy_threshold, 0.008) * 0.55)

    silent_runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for start in range(0, audio.size, frame_samples):
        frame = audio[start:start + frame_samples].astype(np.float32) / 32768.0
        if frame.size == 0:
            continue
        rms = float(np.sqrt(np.mean(frame * frame)))
        if rms < energy_threshold:
            if run_start is None:
                run_start = start
        elif run_start is not None:
            if (start - run_start) // frame_samples >= silence_frames_needed:
                silent_runs.append((run_start, start))
            run_start = None
    if run_start is not None and (audio.size - run_start) // frame_samples >= silence_frames_needed:
        silent_runs.append((run_start, audio.size))

    split_points = [(start + end) // 2 for start, end in silent_runs]
    segments: list[bytes] = []
    start = 0
    while start < total_samples:
        remaining = total_samples - start
        if remaining <= max_samples:
            end = total_samples
        else:
            lower = start + min_samples
            upper = start + max_samples
            candidates = [point for point in split_points if lower <= point <= upper]
            end = candidates[-1] if candidates else upper

        if end <= start:
            break
        segment = pcm_bytes[start * 2:end * 2]
        if len(segment) >= min_samples * 2 or not segments:
            segments.append(segment)
        elif segments:
            segments[-1] += segment
        start = end

    if len(segments) > 1:
        logger.info(
            "长语音段已按停顿/时长切分：%.1fs -> %s",
            total_samples / PCM_SAMPLE_RATE,
            [round((len(item) // 2) / PCM_SAMPLE_RATE, 1) for item in segments],
        )
    return segments or [pcm_bytes]


def unknown_speaker_dir(session_id: str) -> Path:
    path = storage.sessions_dir / f"{session_id}_unknown_speakers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def identify_unknown_speaker(session: Session, wav_path: Path) -> tuple[str, float]:
    # 过短音频不做声纹聚类，复用当前会议最近的 unknown speaker，避免误聚类。
    if wav_duration_seconds(wav_path) < MIN_SPEAKER_EMBEDDING_SECONDS:
        return reusable_unknown_speaker_id(session), 0.0

    speaker_dir = unknown_speaker_dir(session.id)
    embedding_files = {
        path.stem: path
        for path in speaker_dir.glob("voice_*.npy")
    }
    try:
        speaker_id, score, embedding = speaker_service.identify_embedding(
            wav_path,
            embedding_files,
            runtime_config.get().speaker_cluster_threshold,
        )
    except Exception as exc:
        # 声纹模型下载/加载失败时，不应阻断 ASR 结果返回；降级复用最近 unknown speaker。
        logger.warning("未知说话人声纹聚类失败，降级复用最近 unknown speaker：%s", exc)
        return reusable_unknown_speaker_id(session), 0.0
    if speaker_id:
        return speaker_id, score

    speaker_id = f"voice_{uuid4().hex[:8]}"
    np.save(speaker_dir / f"{speaker_id}.npy", embedding)
    return speaker_id, score


def collect_speaker_fields(
    session: Session,
    wav_path: Path,
    speaker_identity_id: str | None = None,
) -> dict:
    """同步执行声纹识别，由调用方放到线程池中运行，避免阻塞 WebSocket 事件循环。"""
    # 过短音频不做声纹识别，避免 ECAPA 在短音频上的不可靠结果误匹配/误聚类。
    if 0 < wav_duration_seconds(wav_path) < MIN_SPEAKER_EMBEDDING_SECONDS:
        return {
            "participant_id": None,
            "speaker_identity_id": speaker_identity_id or reusable_unknown_speaker_id(session),
            "speaker_name": "未知说话人",
            "role": "unknown",
            "need_reply": False,
            "score": 0.0,
        }

    participants = load_participants()
    try:
        speaker, score = speaker_service.identify(wav_path, participants)
    except Exception as exc:
        # 声纹模型下载/加载失败时不应阻断 ASR 结果返回。
        logger.warning("参会人声纹识别失败，降级为未知说话人：%s", exc)
        speaker, score = None, 0.0

    if speaker:
        return {
            "participant_id": speaker.id,
            "speaker_identity_id": speaker.id,
            "speaker_name": speaker.name,
            "role": speaker.role,
            "need_reply": speaker.need_reply,
            "score": score,
        }

    clustered_id, cluster_score = identify_unknown_speaker(session, wav_path)
    return {
        "participant_id": None,
        "speaker_identity_id": speaker_identity_id or clustered_id,
        "speaker_name": "未知说话人",
        "role": "unknown",
        "need_reply": False,
        "score": max(score, cluster_score),
    }


def structured_context_for_session(session: Session) -> str:
    """从本地 Agent 记忆生成供远程 LLM 使用的结构化上下文。"""
    memory = context_manager.load_memory(session.id, session.goal)
    return context_manager.format_context_for_llm(memory)


def restore_asr_text(text: str) -> str:
    return restore_final_punctuation(text)


async def create_message_from_text_audio(
    session: Session,
    text: str,
    wav_path: Path,
    speaker_identity_id: str | None = None,
) -> Message:
    text = restore_asr_text(text)
    fields = await asyncio.to_thread(collect_speaker_fields, session, wav_path, speaker_identity_id)
    analysis = context_manager.analyze_utterance(text)

    suggestion = None
    # 只有“指定参会人”且“本地判断为问题/明确请求”才触发 AI 回复。
    # 说明、示例、解释等内容仍会进入记忆，但不生成建议回复。
    if context_manager.should_trigger_reply(fields["need_reply"], analysis):
        history = "\n".join([f"{m.speaker_name}: {m.text}" for m in session.messages[-8:]])
        suggestion = await llm_client.suggest_reply(
            session.goal,
            history,
            text,
            structured_context=structured_context_for_session(session),
        )

    message = Message(
        id=f"message_{uuid4().hex[:10]}",
        session_id=session.id,
        participant_id=fields["participant_id"],
        speaker_identity_id=fields["speaker_identity_id"],
        speaker_name=fields["speaker_name"],
        role=fields["role"],  # type: ignore[arg-type]
        text=text,
        audio_file=str(wav_path),
        speaker_score=fields["score"],
        need_reply=fields["need_reply"],
        ai_suggestion=suggestion,
        created_at=now_iso(),
        utterance_labels=analysis.labels,
        should_reply=analysis.should_reply,
        analysis_reason=analysis.reason,
    )
    session.messages.append(message)
    save_session(session)
    context_manager.update_with_message(session, message, analysis=analysis)
    return message


def resolve_external_speaker_fields(payload: ExternalTranscriptCreate) -> dict:
    participants = load_participants()
    if payload.participant_id:
        participant = next((item for item in participants if item.id == payload.participant_id), None)
        if not participant:
            raise HTTPException(status_code=404, detail="参会人不存在。")
        return {
            "participant_id": participant.id,
            "speaker_identity_id": participant.id,
            "speaker_name": participant.name,
            "role": participant.role,
            "need_reply": participant.need_reply,
        }

    speaker_name = (payload.speaker_name or "").strip() or "外部 ASR"
    return {
        "participant_id": None,
        "speaker_identity_id": None,
        "speaker_name": speaker_name,
        "role": payload.role,
        "need_reply": False,
    }


async def create_message_from_external_transcript(
    session: Session,
    payload: ExternalTranscriptCreate,
) -> Message:
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="转写文本不能为空。")

    fields = resolve_external_speaker_fields(payload)
    analysis = context_manager.analyze_utterance(text)
    suggestion = None
    if context_manager.should_trigger_reply(fields["need_reply"], analysis):
        history = "\n".join([f"{m.speaker_name}: {m.text}" for m in session.messages[-8:]])
        suggestion = await llm_client.suggest_reply(
            session.goal,
            history,
            text,
            structured_context=structured_context_for_session(session),
        )

    message = Message(
        id=f"message_{uuid4().hex[:10]}",
        session_id=session.id,
        participant_id=fields["participant_id"],
        speaker_identity_id=fields["speaker_identity_id"],
        speaker_name=fields["speaker_name"],
        role=fields["role"],
        text=text,
        speaker_score=None,
        need_reply=fields["need_reply"],
        ai_suggestion=suggestion,
        created_at=payload.recognized_at or now_iso(),
        utterance_labels=analysis.labels,
        should_reply=analysis.should_reply,
        analysis_reason=analysis.reason,
        source=payload.source,
    )
    session.messages.append(message)
    save_session(session)
    context_manager.update_with_message(session, message, analysis=analysis)
    return message


async def create_messages_from_pcm_segment(
    session: Session,
    original_text: str,
    pcm_bytes: bytes,
    audio_prefix: str,
    speaker_identity_id: str | None = None,
) -> list[Message]:
    """从一段 PCM final 音频创建消息；长段会先二次切分并逐段 ASR/声纹识别。"""
    audio_dir = storage.session_audio_dir(session.id)
    duration_seconds = (len(pcm_bytes) // 2) / PCM_SAMPLE_RATE if pcm_bytes else 0.0
    source_wav_path = audio_dir / f"{audio_prefix}.wav"

    # 优先尝试 FunASR 句级 speaker diarization。它能处理“同一段音频里无明显静音换人”的情况，
    # 比纯 VAD/固定长度切分更适合区分记者/嘉宾、男声/女声交替。
    if duration_seconds >= 4.0:
        write_pcm16_wav(source_wav_path, pcm_bytes)
        diarized_segments = await asyncio.to_thread(transcribe_speaker_segments, source_wav_path)
        diarized_speakers = {str(item.get("spk")) for item in diarized_segments if item.get("spk") is not None}
        if len(diarized_segments) > 1 and (len(diarized_speakers) > 1 or duration_seconds > MAX_SINGLE_SPEAKER_SEGMENT_SECONDS):
            messages: list[Message] = []
            spk_to_voice_id: dict[str, str] = {}
            for index, item in enumerate(diarized_segments):
                child_text = str(item.get("text", "")).strip()
                if not child_text:
                    continue
                start = float(item.get("start", 0.0) or 0.0)
                end = float(item.get("end", 0.0) or 0.0)
                child_pcm = slice_pcm16(pcm_bytes, start, end) if end > start else b""
                if not child_pcm:
                    continue

                spk_key = str(item.get("spk", f"segment_{index}"))
                forced_speaker_id = spk_to_voice_id.get(spk_key)
                child_duration = (len(child_pcm) // 2) / PCM_SAMPLE_RATE
                if forced_speaker_id is None and child_duration < MIN_SPEAKER_EMBEDDING_SECONDS:
                    # diarization 已判断这是一个独立 speaker；即使片段短，也不要复用最近说话人。
                    forced_speaker_id = f"voice_{uuid4().hex[:8]}"
                    spk_to_voice_id[spk_key] = forced_speaker_id

                child_wav_path = audio_dir / f"{audio_prefix}_spk{spk_key}_{index + 1}.wav"
                write_pcm16_wav(child_wav_path, child_pcm)
                message = await create_message_from_text_audio(session, child_text, child_wav_path, forced_speaker_id)
                if message.speaker_identity_id and spk_key not in spk_to_voice_id:
                    spk_to_voice_id[spk_key] = message.speaker_identity_id
                messages.append(message)
            if messages:
                return messages

    split_segments = split_pcm16_for_speaker_turns(pcm_bytes)
    messages: list[Message] = []

    if len(split_segments) <= 1:
        if not source_wav_path.exists():
            write_pcm16_wav(source_wav_path, pcm_bytes)
        text = original_text.strip()
        if runtime_config.get().bilingual_asr_enabled or not text:
            transcript_segments = await asyncio.to_thread(transcribe_audio_file, str(source_wav_path))
            enhanced_text = "".join(segment.get("text", "") for segment in transcript_segments).strip()
            text = enhanced_text or text
        if text:
            messages.append(await create_message_from_text_audio(session, text, source_wav_path, speaker_identity_id))
        return messages

    for index, child_pcm in enumerate(split_segments):
        wav_path = audio_dir / f"{audio_prefix}_part{index + 1}.wav"
        write_pcm16_wav(wav_path, child_pcm)
        transcript_segments = await asyncio.to_thread(transcribe_audio_file, str(wav_path))
        child_text = "".join(segment.get("text", "") for segment in transcript_segments).strip()
        if not child_text:
            continue
        messages.append(await create_message_from_text_audio(session, child_text, wav_path, None))

    if not messages:
        wav_path = audio_dir / f"{audio_prefix}.wav"
        write_pcm16_wav(wav_path, pcm_bytes)
        messages.append(await create_message_from_text_audio(session, original_text, wav_path, speaker_identity_id))
    return messages


async def process_audio_file(session_id: str, file: UploadFile) -> list[Message]:
    session = load_session(session_id)
    message_id = f"message_{uuid4().hex[:10]}"
    audio_dir = storage.session_audio_dir(session_id)
    raw_path = audio_dir / f"{message_id}.webm"
    wav_path = audio_dir / f"{message_id}.wav"

    raw_path.write_bytes(await file.read())
    try:
        audio_service.convert_to_wav(raw_path, wav_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"音频格式无法解析：{exc}") from exc

    try:
        new_messages = await create_messages_from_pcm_segment(session, "", read_wav_pcm16(wav_path), message_id, None)
        if new_messages:
            return new_messages
    except Exception as exc:
        logger.warning("上传音频说话人分离失败，降级到普通 ASR：%s", exc)

    # ASR 与声纹推理都是 CPU 密集任务，放到线程池，避免阻塞事件循环。
    transcript_segments = await asyncio.to_thread(transcribe_audio_file, str(wav_path))
    if not transcript_segments:
        raise HTTPException(status_code=400, detail="未识别到有效语音")

    fields = await asyncio.to_thread(collect_speaker_fields, session, wav_path, None)

    new_messages: list[Message] = []
    for index, segment in enumerate(transcript_segments):
        text = restore_asr_text(segment["text"])
        analysis = context_manager.analyze_utterance(text)
        suggestion = None
        if context_manager.should_trigger_reply(fields["need_reply"], analysis):
            history = "\n".join([f"{m.speaker_name}: {m.text}" for m in session.messages[-8:]])
            suggestion = await llm_client.suggest_reply(
                session.goal,
                history,
                text,
                structured_context=structured_context_for_session(session),
            )

        message = Message(
            id=f"{message_id}_{index + 1}",
            session_id=session_id,
            participant_id=fields["participant_id"],
            speaker_identity_id=fields["speaker_identity_id"],
            speaker_name=fields["speaker_name"],
            role=fields["role"],  # type: ignore[arg-type]
            text=text,
            start_time=segment["start"],
            end_time=segment["end"],
            audio_file=str(wav_path),
            speaker_score=fields["score"],
            need_reply=fields["need_reply"],
            ai_suggestion=suggestion,
            created_at=now_iso(),
            utterance_labels=analysis.labels,
            should_reply=analysis.should_reply,
            analysis_reason=analysis.reason,
        )
        new_messages.append(message)
        session.messages.append(message)
        # 逐段更新本地记忆，使后续分段能看到前面分段的上下文。
        context_manager.update_with_message(session, message, analysis=analysis)

    save_session(session)
    return new_messages


async def backfill_ai_suggestions_for_bound_speaker(session: Session, speaker_identity_id: str) -> None:
    """绑定/修正说话人后，为历史中已满足触发条件的问题补生成 AI 回复。"""
    changed = False
    for index, message in enumerate(session.messages):
        identity = message.speaker_identity_id or message.participant_id or f"voice_{message.id}"
        if identity != speaker_identity_id:
            continue
        if not (message.need_reply and message.should_reply) or message.ai_suggestion:
            continue

        history = "\n".join([f"{m.speaker_name}: {m.text}" for m in session.messages[max(0, index - 8):index]])
        data = message.model_dump()
        data["ai_suggestion"] = await llm_client.suggest_reply(
            session.goal,
            history,
            message.text,
            structured_context=structured_context_for_session(session),
        )
        session.messages[index] = Message(**data)
        changed = True

    if changed:
        save_session(session)


async def bind_speaker_identity(session_id: str, speaker_identity_id: str, payload: SpeakerBindRequest) -> SpeakerBindResponse:
    session = load_session(session_id)
    updated_count = 0
    for index, message in enumerate(session.messages):
        identity = message.speaker_identity_id or message.participant_id or f"voice_{message.id}"
        if identity != speaker_identity_id:
            continue
        data = message.model_dump()
        data["speaker_identity_id"] = speaker_identity_id
        data["speaker_name"] = payload.speaker_name
        data["role"] = payload.role
        data["need_reply"] = payload.need_reply
        data["corrected"] = True
        session.messages[index] = Message(**data)
        updated_count += 1

    if updated_count == 0:
        raise HTTPException(status_code=404, detail="识别个体 ID 不存在")

    save_session(session)
    context_manager.rebuild(session)
    if payload.need_reply:
        await backfill_ai_suggestions_for_bound_speaker(session, speaker_identity_id)
    return SpeakerBindResponse(
        speaker_identity_id=speaker_identity_id,
        speaker_name=payload.speaker_name,
        updated_count=updated_count,
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/participants", response_model=list[Participant])
def get_participants() -> list[Participant]:
    return load_participants()


@app.get("/api/config", response_model=ModelConfig)
def get_config() -> ModelConfig:
    return mask_config(runtime_config.get())


@app.put("/api/config", response_model=ModelConfig)
def update_config(payload: ModelConfig) -> ModelConfig:
    previous = runtime_config.get()
    saved = runtime_config.save(unmask_api_key(payload))
    if (
        previous.punctuation_model != saved.punctuation_model
        or previous.punctuation_device != saved.punctuation_device
    ):
        get_punctuation_model.cache_clear()
    if previous.speaker_diarization_enabled != saved.speaker_diarization_enabled:
        get_speaker_diarization_model.cache_clear()
    return mask_config(saved)


@app.post("/api/config/test", response_model=ConnectionTestResponse)
async def test_config_connection(payload: ModelConfig | None = Body(default=None)) -> ConnectionTestResponse:
    config = unmask_api_key(payload) if payload is not None else runtime_config.get()
    result = await llm_client.test_connection(config)
    return ConnectionTestResponse(**result)


@app.post("/api/meetings", response_model=Session)
def create_meeting(payload: MeetingCreate) -> Session:
    title = f"{payload.customer_name.strip()}会谈"
    return create_session_from_title_goal(title, payload.goal)


@app.get("/api/meetings", response_model=list[SessionSummary])
def list_meetings() -> list[SessionSummary]:
    return list_sessions()


@app.get("/api/meetings/{meeting_id}", response_model=Session)
def get_meeting(meeting_id: str) -> Session:
    return load_session(meeting_id)


@app.post("/api/meetings/{meeting_id}/audio", response_model=list[Message])
async def upload_meeting_audio(meeting_id: str, file: UploadFile = File(...)) -> list[Message]:
    return await process_audio_file(meeting_id, file)


@app.post("/api/meetings/{meeting_id}/transcripts", response_model=Message)
async def add_external_transcript(
    meeting_id: str,
    payload: ExternalTranscriptCreate,
    authorization: str | None = Header(default=None),
) -> Message:
    """接收外部系统 ASR 已识别文本。

    发言人由客户端手动选择，本接口不上传音频、不做声纹识别，降低本地采集端隐私风险。
    """
    verify_transcript_ingest_token(authorization)
    session = load_session(meeting_id)
    return await create_message_from_external_transcript(session, payload)


@app.websocket("/api/meetings/{meeting_id}/asr/funasr/stream")
async def stream_meeting_audio_funasr(websocket: WebSocket, meeting_id: str) -> None:
    await websocket.accept()
    session = load_session(meeting_id)
    try:
        await websocket.send_json({"type": "loading", "message": "FunASR 模型加载中，首次运行可能需要较长时间..."})
        stream_session = await asyncio.to_thread(funasr_streaming_service.create_session)
        await websocket.send_json({"type": "ready", "message": "FunASR streaming ready"})
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close()
        except Exception:
            return
        return

    segment_pcm = bytearray()
    pending_pcm = bytearray()
    segment_text = ""
    speaker_identity_id: str | None = None
    # 必须与 FunASRStreamingSession.chunk_size=[0,10,5] 一致：
    # chunk_stride = 10 * 960 = 9600 samples = 600ms @16kHz。
    # 之前用 1200ms chunk 破坏了 decoder lookback 的流式约定，导致识别碎、语义不连贯。
    funasr_chunk_bytes = 16000 * 2 * 600 // 1000

    async def finalize_segment() -> None:
        nonlocal segment_pcm, pending_pcm, segment_text, speaker_identity_id, session
        if pending_pcm:
            final_text = await asyncio.to_thread(stream_session.transcribe_pcm16, bytes(pending_pcm), True)
            if final_text:
                segment_text = merge_stream_text(segment_text, final_text)
            pending_pcm = bytearray()

        # 一个语义段结束后清空流式状态，否则前一段的 encoder/decoder 缓存会污染下一段。
        stream_session.reset()

        text = segment_text.strip()
        if not text or not segment_pcm:
            segment_pcm = bytearray()
            segment_text = ""
            speaker_identity_id = None
            return

        messages = await create_messages_from_pcm_segment(
            session,
            text,
            bytes(segment_pcm),
            f"message_{uuid4().hex[:10]}_funasr",
            speaker_identity_id,
        )
        for message in messages:
            await websocket.send_json({"type": "final", "message": message.model_dump()})
        segment_pcm = bytearray()
        segment_text = ""
        speaker_identity_id = None
        session = load_session(meeting_id)

    try:
        while True:
            event = await websocket.receive()
            if event.get("type") == "websocket.disconnect":
                break

            if event.get("bytes") is not None:
                pcm = event["bytes"]
                segment_pcm.extend(pcm)
                pending_pcm.extend(pcm)
                while len(pending_pcm) >= funasr_chunk_bytes:
                    chunk = bytes(pending_pcm[:funasr_chunk_bytes])
                    del pending_pcm[:funasr_chunk_bytes]
                    text = await asyncio.to_thread(stream_session.transcribe_pcm16, chunk, False)
                    if text:
                        segment_text = merge_stream_text(segment_text, text)
                        await websocket.send_json({"type": "partial", "text": segment_text})
                continue

            if event.get("text") is not None:
                import json

                payload = json.loads(event["text"])
                if payload.get("type") == "final":
                    await finalize_segment()
                elif payload.get("type") == "close":
                    await finalize_segment()
                    await websocket.close()
                    return
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            return


@app.put("/api/meetings/{meeting_id}/speakers/{speaker_identity_id}", response_model=SpeakerBindResponse)
async def bind_meeting_speaker(
    meeting_id: str,
    speaker_identity_id: str,
    payload: SpeakerBindRequest,
) -> SpeakerBindResponse:
    return await bind_speaker_identity(meeting_id, speaker_identity_id, payload)


@app.post("/api/meetings/{meeting_id}/end", response_model=Session)
def end_meeting(meeting_id: str) -> Session:
    session = load_session(meeting_id)
    session.ended_at = session.ended_at or now_iso()
    save_session(session)
    return session


@app.post("/api/meetings/{meeting_id}/summary", response_model=SummaryResponse)
async def summarize_meeting(meeting_id: str) -> SummaryResponse:
    return await summarize_session(meeting_id)


@app.post("/api/meetings/{meeting_id}/export", response_model=ExportResponse)
def export_meeting(meeting_id: str) -> ExportResponse:
    data = export_session(meeting_id)
    return ExportResponse(**data)


@app.delete("/api/meetings/{meeting_id}/messages", response_model=Session)
def clear_meeting_messages(meeting_id: str) -> Session:
    session = load_session(meeting_id)
    session.messages = []
    session.summary = None
    save_session(session)
    context_manager.rebuild(session)
    return session


@app.get("/api/meetings/{meeting_id}/agent/memory", response_model=AgentMemory)
def get_agent_memory(meeting_id: str) -> AgentMemory:
    session = load_session(meeting_id)
    return context_manager.load_memory(session.id, session.goal)


@app.post("/api/meetings/{meeting_id}/agent/run", response_model=AgentRunResponse)
def run_meeting_agent(meeting_id: str) -> AgentRunResponse:
    session = load_session(meeting_id)
    memory = context_manager.rebuild(session)
    return AgentRunResponse(memory=memory, actions=memory.decisions[-20:])


@app.post("/api/participants", response_model=Participant)
def create_participant(payload: ParticipantCreate) -> Participant:
    participant_id = f"participant_{uuid4().hex[:8]}"
    color = "yellow" if payload.role == "self" else "white" if payload.role == "customer" else "gray"
    participant = Participant(
        id=participant_id,
        name=payload.name,
        role=payload.role,
        need_reply=payload.need_reply,
        display_color=color,
        remark=payload.remark,
    )
    participants = load_participants()
    participants.append(participant)
    save_participants(participants)
    return participant


@app.put("/api/participants/{participant_id}", response_model=Participant)
def update_participant(participant_id: str, payload: ParticipantUpdate) -> Participant:
    participants = load_participants()
    for index, participant in enumerate(participants):
        if participant.id == participant_id:
            data = participant.model_dump()
            for key, value in payload.model_dump(exclude_unset=True).items():
                data[key] = value
            if data.get("role") == "self":
                data["display_color"] = "yellow"
            elif data.get("role") == "customer":
                data["display_color"] = "white"
            else:
                data["display_color"] = "gray"
            updated = Participant(**data)
            participants[index] = updated
            save_participants(participants)
            return updated
    raise HTTPException(status_code=404, detail="参会人不存在")


@app.post("/api/participants/{participant_id}/voiceprint", response_model=Participant)
async def upload_voiceprint(participant_id: str, file: UploadFile = File(...)) -> Participant:
    participants = load_participants()
    participant = next((p for p in participants if p.id == participant_id), None)
    if not participant:
        raise HTTPException(status_code=404, detail="参会人不存在")

    raw_path = storage.participants_dir / f"{participant_id}_sample.webm"
    wav_path = storage.participants_dir / f"{participant_id}_sample.wav"
    embedding_path = storage.participants_dir / f"{participant_id}.embedding.npy"

    raw_path.write_bytes(await file.read())
    audio_service.convert_to_wav(raw_path, wav_path)
    speaker_service.save_embedding(wav_path, embedding_path)

    participant.sample_audio_file = str(wav_path)
    participant.voiceprint_file = str(embedding_path)

    for index, item in enumerate(participants):
        if item.id == participant_id:
            participants[index] = participant
            break
    save_participants(participants)
    return participant


@app.post("/api/sessions", response_model=Session)
def create_session(payload: SessionCreate) -> Session:
    return create_session_from_title_goal(payload.title, payload.goal)


@app.get("/api/sessions", response_model=list[SessionSummary])
def list_sessions() -> list[SessionSummary]:
    sessions: list[SessionSummary] = []
    for path in sorted(storage.sessions_dir.glob("*.json"), reverse=True):
        if path.name.endswith(".memory.json"):
            continue
        data = storage.read_json(path)
        if not data:
            continue
        session = Session(**data)
        sessions.append(
            SessionSummary(
                id=session.id,
                title=session.title,
                goal=session.goal,
                started_at=session.started_at,
                ended_at=session.ended_at,
                message_count=len(session.messages),
                has_summary=bool(session.summary),
            )
        )
    return sessions


@app.get("/api/sessions/{session_id}", response_model=Session)
def get_session(session_id: str) -> Session:
    return load_session(session_id)


@app.put("/api/sessions/{session_id}/messages/{message_id}/speaker", response_model=Message)
async def correct_message_speaker(
    session_id: str,
    message_id: str,
    payload: SpeakerCorrectionRequest,
) -> Message:
    session = load_session(session_id)
    participants = load_participants()
    participant = None
    if payload.participant_id:
        participant = next((p for p in participants if p.id == payload.participant_id), None)
        if not participant:
            raise HTTPException(status_code=404, detail="参会人不存在")

    for index, message in enumerate(session.messages):
        if message.id != message_id:
            continue

        data = message.model_dump()
        if participant:
            data.update(participant_to_message_fields(participant))
        else:
            data["participant_id"] = None
            data["speaker_name"] = payload.speaker_name or "未知说话人"
            data["role"] = payload.role or "unknown"
            data["need_reply"] = False
        data["corrected"] = True

        updated = Message(**data)
        if updated.need_reply and not updated.ai_suggestion:
            # 人工修正为“需要答复”属于显式意图，直接生成，但仍带上本地归纳的上下文。
            history = "\n".join([f"{m.speaker_name}: {m.text}" for m in session.messages[max(0, index - 8):index]])
            updated.ai_suggestion = await llm_client.suggest_reply(
                session.goal,
                history,
                updated.text,
                structured_context=structured_context_for_session(session),
            )

        session.messages[index] = updated
        save_session(session)
        return updated

    raise HTTPException(status_code=404, detail="消息不存在")


@app.post("/api/audio/process", response_model=Message)
async def process_audio(
    session_id: str = Form(...),
    file: UploadFile = File(...),
) -> Message:
    messages = await process_audio_file(session_id, file)
    return messages[0]


@app.post("/api/ai/suggest-reply", response_model=SuggestReplyResponse)
async def suggest_reply(payload: SuggestReplyRequest) -> SuggestReplyResponse:
    suggestion = await llm_client.suggest_reply(
        payload.meeting_goal,
        payload.conversation_history,
        payload.latest_customer_message,
    )
    return SuggestReplyResponse(suggestion=suggestion)


@app.post("/api/sessions/{session_id}/summary", response_model=SummaryResponse)
async def summarize_session(session_id: str) -> SummaryResponse:
    session = load_session(session_id)
    if not session.messages:
        raise HTTPException(status_code=400, detail="当前会话还没有可总结的内容")

    history = "\n".join([f"{m.speaker_name}: {m.text}" for m in session.messages])
    summary = await llm_client.summarize(
        session.goal,
        history,
        structured_context=structured_context_for_session(session),
    )
    session.summary = summary
    session.ended_at = session.ended_at or now_iso()
    save_session(session)
    return SummaryResponse(summary=summary)


@app.post("/api/sessions/{session_id}/export", response_model=ExportResponse)
def export_session(session_id: str) -> dict:
    session = load_session(session_id)
    lines = [
        f"# {session.title}",
        "",
        f"- 会晤目标：{session.goal}",
        f"- 开始时间：{session.started_at}",
        f"- 结束时间：{session.ended_at or ''}",
        "",
        "## 会话记录",
        "",
    ]
    for message in session.messages:
        lines.append(f"### {message.speaker_name}")
        lines.append("")
        lines.append(message.text)
        lines.append("")
        if message.ai_suggestion:
            lines.append(f"> AI 建议：{message.ai_suggestion}")
            lines.append("")

    if session.summary:
        lines.extend(["## 会后总结", "", session.summary, ""])

    export_path = storage.exports_dir / f"{session_id}.md"
    export_path.write_text("\n".join(lines), encoding="utf-8")
    return {"file": str(export_path)}
