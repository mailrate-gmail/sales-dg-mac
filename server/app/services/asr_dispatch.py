"""短音频 ASR 后端分派。

独立于 main.py，便于单元测试；重型模型仅在对应分支被调用时才加载。
"""


import logging

from fastapi import HTTPException

from app.services.runtime_config import runtime_config

logger = logging.getLogger(__name__)


def _transcribe_with_faster_whisper(wav_path: str, language: str | None = "zh") -> list[dict]:
    from app.services.asr import asr_service

    return asr_service.transcribe_segments(wav_path, language=language)


def _transcribe_with_funasr(wav_path: str) -> list[dict]:
    from app.services.funasr_streaming import funasr_streaming_service

    session = funasr_streaming_service.create_session()
    text = session.transcribe_wav_file(wav_path)  # type: ignore[arg-type]
    if not text:
        return []
    return [{"text": text, "start": 0.0, "end": 0.0}]


def transcribe_audio_file(wav_path: str) -> list[dict]:
    """按 asr_provider 配置分派短音频识别，返回带 text/start/end 的分段列表。

    - faster_whisper：本地 faster-whisper，返回多个带时间戳分段；
    - funasr_streaming：本地 FunASR 流式模型整段识别（无段级时间戳）；
    - oci_speech：尚未接入，返回 501，避免静默回退造成误解。

    采用惰性导入，避免仅做分派判断时就加载 numpy / torch / speechbrain 等重型依赖。
    """
    config = runtime_config.get()
    provider = config.asr_provider
    whisper_language = None if config.bilingual_asr_enabled else "zh"
    if provider == "faster_whisper":
        return _transcribe_with_faster_whisper(wav_path, language=whisper_language)
    if provider == "funasr_streaming":
        if config.bilingual_asr_enabled:
            try:
                enhanced = _transcribe_with_faster_whisper(wav_path, language=None)
                if enhanced:
                    return enhanced
            except Exception as exc:
                logger.warning("双语识别增强不可用，降级到 FunASR streaming：%s", exc)
        return _transcribe_with_funasr(wav_path)
    if provider == "oci_speech":
        raise HTTPException(status_code=501, detail="OCI Speech 尚未接入，请改用本地识别后端。")
    raise HTTPException(status_code=400, detail=f"不支持的 ASR Provider：{provider}")
