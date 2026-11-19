import json
import threading
import wave
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.services.runtime_config import runtime_config

# 全局模型实例由 lru_cache 共享；多个 WebSocket 会话会在不同线程中并发调用
# model.generate()。FunASR 模型内部状态不是线程安全的，必须串行化。
_model_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_streaming_model() -> Any:
    try:
        from funasr import AutoModel
    except ImportError as exc:
        raise RuntimeError("FunASR 未安装，请安装 funasr 依赖。") from exc

    config = runtime_config.get()
    model_name = config.funasr_model or "paraformer-zh-streaming"
    if "/" not in model_name:
        model_name = f"funasr/{model_name}"

    # 不加载 fsmn-vad：本项目按需求由前端做“语音停顿”端点检测（VAD），
    # 服务端再加一层 VAD 会造成双重切分。paraformer-zh-streaming 本身支持 chunk 流式输入。
    return AutoModel(
        model=model_name,
        hub="hf",
        model_revision="main",
        device=config.funasr_device,
        disable_update=True,
    )


class FunASRStreamingSession:
    def __init__(self) -> None:
        self.model = get_streaming_model()
        self.cache: dict = {}
        # [0, 10, 5] => chunk_stride = 10 * 960 = 9600 samples = 600ms @16kHz。
        # 每次 generate 的输入必须与 chunk_stride 对齐（最后一段可短），否则流式状态会错位。
        self.chunk_size = [0, 10, 5]
        self.encoder_chunk_look_back = 4
        self.decoder_chunk_look_back = 1

    def transcribe_pcm16(self, pcm_bytes: bytes, is_final: bool = False) -> str:
        if not pcm_bytes:
            return ""

        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        with _model_lock:
            result = self.model.generate(
                input=audio,
                cache=self.cache,
                is_final=is_final,
                chunk_size=self.chunk_size,
                encoder_chunk_look_back=self.encoder_chunk_look_back,
                decoder_chunk_look_back=self.decoder_chunk_look_back,
            )
        return self.extract_text(result)

    def reset(self) -> None:
        """一个语义段结束后清空流式状态，避免前一段缓存污染下一段。"""
        self.cache = {}

    def extract_text(self, result: Any) -> str:
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, list) and result:
            item = result[0]
            if isinstance(item, dict):
                return str(item.get("text", "")).strip()
            return str(item).strip()
        if isinstance(result, dict):
            return str(result.get("text", "")).strip()
        try:
            data = json.loads(str(result))
            return self.extract_text(data)
        except Exception:
            return ""

    def transcribe_wav_file(self, wav_path: Path) -> str:
        """一次性转写整段 16kHz PCM16 wav，供 HTTP 短音频 fallback 使用。

        仍按 600ms chunk 送入以匹配 [0,10,5] 的流式约定，最后一段以 is_final=True 收尾。
        """
        with wave.open(str(wav_path), "rb") as wav:
            pcm = wav.readframes(wav.getnframes())

        chunk_bytes = 16000 * 2 * 600 // 1000
        parts: list[str] = []
        offset = 0
        while offset < len(pcm):
            chunk = pcm[offset : offset + chunk_bytes]
            offset += chunk_bytes
            is_final = offset >= len(pcm)
            text = self.transcribe_pcm16(chunk, is_final)
            if text:
                parts.append(text)
        self.reset()
        return "".join(parts).strip()


class FunASRStreamingService:
    def create_session(self) -> FunASRStreamingSession:
        return FunASRStreamingSession()



funasr_streaming_service = FunASRStreamingService()
