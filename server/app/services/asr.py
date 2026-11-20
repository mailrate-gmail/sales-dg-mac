from functools import lru_cache
from typing import TypedDict

from faster_whisper import WhisperModel

from app.services.runtime_config import runtime_config


class TranscribeSegment(TypedDict):
    text: str
    start: float
    end: float


@lru_cache(maxsize=2)
def get_model(model_name: str, device: str, compute_type: str) -> WhisperModel:
    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
    )


class ASRService:
    def transcribe_segments(self, audio_path: str, language: str | None = "zh") -> list[TranscribeSegment]:
        config = runtime_config.get()
        model = get_model(config.whisper_model, config.whisper_device, config.whisper_compute_type)
        vad_silence_ms = config.silence_duration_ms
        transcribe_kwargs = {
            "vad_filter": True,
            "vad_parameters": {
                "min_silence_duration_ms": vad_silence_ms,
                "speech_pad_ms": 200,
            },
            "condition_on_previous_text": False,
            "beam_size": 5,
        }
        if language:
            transcribe_kwargs["language"] = language
        segments, _ = model.transcribe(
            audio_path,
            **transcribe_kwargs,
        )
        results: list[TranscribeSegment] = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                results.append({"text": text, "start": float(segment.start), "end": float(segment.end)})
        return results

    def transcribe(self, audio_path: str, language: str | None = "zh") -> str:
        text_parts = [segment["text"] for segment in self.transcribe_segments(audio_path, language=language)]
        return "".join(text_parts).strip()


asr_service = ASRService()
