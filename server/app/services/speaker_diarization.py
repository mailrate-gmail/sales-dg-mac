"""FunASR 句级说话人分离（speaker diarization）。

用于补足前端 VAD / 服务端静音切分无法处理的场景：同一个 final 音频片段里包含
多位说话人，且中间没有足够长的静音。该模块只在 final 后处理阶段惰性加载。
"""

import logging
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services.runtime_config import runtime_config

logger = logging.getLogger(__name__)

_diarization_lock = threading.Lock()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _hf_funasr_model(name: str) -> str:
    return name if "/" in name else f"funasr/{name}"


def _extract_sentence_info(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list) and result:
        first = result[0]
        if isinstance(first, dict):
            info = first.get("sentence_info") or first.get("sentences") or []
            return info if isinstance(info, list) else []
    if isinstance(result, dict):
        info = result.get("sentence_info") or result.get("sentences") or []
        return info if isinstance(info, list) else []
    return []


class FunASRSpeakerDiarizationModel:
    def __init__(self) -> None:
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError("FunASR 未安装，请安装 funasr 和 modelscope 依赖。") from exc

        config = runtime_config.get()
        # paraformer-zh + fsmn-vad + ct-punc + campplus 是 FunASR 官方常用的 ASR+VAD+标点+说话人组合。
        # 使用 HuggingFace 的显式模型路径，避免 ModelScope 在部分网络环境下大文件下载/合并失败。
        self.model = AutoModel(
            model="funasr/paraformer-zh",
            vad_model="funasr/fsmn-vad",
            punc_model=_hf_funasr_model(config.punctuation_model or "ct-punc"),
            spk_model="funasr/campplus",
            hub="hf",
            device=config.funasr_device,
            disable_update=True,
        )

    def transcribe_segments(self, wav_path: Path) -> list[dict[str, Any]]:
        with _diarization_lock:
            result = self.model.generate(input=str(wav_path), batch_size_s=300)

        segments: list[dict[str, Any]] = []
        for item in _extract_sentence_info(result):
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            # FunASR sentence_info 通常以毫秒表示 start/end。
            start = _as_float(item.get("start")) / 1000.0
            end = _as_float(item.get("end")) / 1000.0
            if end <= start:
                end = start
            segments.append({
                "text": text,
                "start": start,
                "end": end,
                "spk": item.get("spk"),
            })
        return segments


@lru_cache(maxsize=1)
def get_speaker_diarization_model() -> FunASRSpeakerDiarizationModel:
    return FunASRSpeakerDiarizationModel()


def transcribe_speaker_segments(wav_path: Path) -> list[dict[str, Any]]:
    if not runtime_config.get().speaker_diarization_enabled:
        return []
    try:
        return get_speaker_diarization_model().transcribe_segments(wav_path)
    except Exception as exc:
        logger.warning("FunASR 说话人分离不可用，降级到普通切分：%s", exc)
        return []
