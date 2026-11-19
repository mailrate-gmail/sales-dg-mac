"""FunASR / HuggingFace 中文标点恢复模型。

模型只在 final 文本处理时惰性加载；partial 文本继续由前端规则轻补，避免实时字幕延迟。
"""

import json
import logging
import threading
from functools import lru_cache
from typing import Any

from app.services.punctuation import restore_punctuation
from app.services.runtime_config import runtime_config

logger = logging.getLogger(__name__)

_model_lock = threading.Lock()


def _extract_text(result: Any) -> str:
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
        return _extract_text(json.loads(str(result)))
    except Exception:
        return ""


def _funasr_hf_punctuation_alias(name: str) -> str:
    """Return a FunASR registry alias that resolves to HuggingFace when hub='hf'.

    FunASR 1.2.x maps the alias ``ct-punc`` to the HuggingFace repository
    ``funasr/ct-punc`` internally. Passing the repository id directly can fail
    registration before the model is cached, so keep the registered alias while
    forcing the hub to HuggingFace.
    """
    return "ct-punc" if name == "funasr/ct-punc" else name


class FunASRPunctuationModel:
    def __init__(self) -> None:
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise RuntimeError("FunASR 未安装，请安装 funasr 依赖。") from exc

        config = runtime_config.get()
        model_name = _funasr_hf_punctuation_alias(config.punctuation_model or "ct-punc")

        # 使用 hub='hf' 强制从 HuggingFace 解析；ct-punc 会映射到 funasr/ct-punc。
        self.model = AutoModel(
            model=model_name,
            hub="hf",
            model_revision="main",
            device=config.punctuation_device,
            disable_update=True,
        )

    def restore(self, text: str) -> str:
        text = text.strip()
        if not text:
            return ""

        with _model_lock:
            result = self.model.generate(input=text)
        restored = _extract_text(result)
        return restored or text


@lru_cache(maxsize=1)
def get_punctuation_model() -> FunASRPunctuationModel:
    return FunASRPunctuationModel()


def restore_final_punctuation(text: str) -> str:
    """按运行时配置恢复 final 文本标点。

    - asr/off：信任 ASR / Whisper / diarization 原始输出，不额外补标点；
    - funasr_punc：优先使用 FunASR 标点模型；模型失败时降级到规则标点；
    - rule：只使用当前规则标点；
    - off：不补标点。
    """
    raw_text = text.strip()
    if not raw_text:
        return ""

    config = runtime_config.get()
    if config.punctuation_provider in {"asr", "off"}:
        return raw_text

    if config.punctuation_provider == "rule":
        return restore_punctuation(raw_text)

    try:
        return get_punctuation_model().restore(raw_text)
    except Exception as exc:
        logger.warning("FunASR 标点模型不可用，降级到规则标点：%s", exc)
        return restore_punctuation(raw_text)
