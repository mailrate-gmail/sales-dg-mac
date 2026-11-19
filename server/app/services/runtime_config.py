import json
from pathlib import Path

from app.config import settings
from app.models import ModelConfig


class RuntimeConfigService:
    def __init__(self) -> None:
        self.path = Path(settings.data_dir) / "model_config.json"

    def default_config(self) -> ModelConfig:
        return ModelConfig(
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            llm_base_url=settings.llm_base_url,
            llm_api_key=settings.llm_api_key,
            llm_timeout_seconds=settings.llm_timeout_seconds,
            asr_provider=settings.asr_provider,  # type: ignore[arg-type]
            whisper_model=settings.whisper_model,
            whisper_device=settings.whisper_device,
            whisper_compute_type=settings.whisper_compute_type,
            funasr_model=settings.funasr_model,
            funasr_device=settings.funasr_device,
            oci_speech_region=settings.oci_speech_region,
            oci_speech_compartment_id=settings.oci_speech_compartment_id,
            oci_speech_language=settings.oci_speech_language,
            oci_speech_profile=settings.oci_speech_profile,
            oci_speech_config_file=settings.oci_speech_config_file,
            oci_speech_realtime_endpoint=settings.oci_speech_realtime_endpoint,
            speaker_threshold=settings.speaker_threshold,
            speaker_cluster_threshold=settings.speaker_cluster_threshold,
            speaker_diarization_enabled=settings.speaker_diarization_enabled,
            energy_threshold=settings.energy_threshold,
            silence_duration_ms=settings.silence_duration_ms,
            min_speech_duration_ms=settings.min_speech_duration_ms,
            punctuation_sensitivity=settings.punctuation_sensitivity,
            punctuation_provider=settings.punctuation_provider,  # type: ignore[arg-type]
            punctuation_model=settings.punctuation_model,
            punctuation_device=settings.punctuation_device,
        )

    def get(self) -> ModelConfig:
        if not self.path.exists():
            return self.default_config()
        with self.path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        merged = self.default_config().model_dump()
        merged.update(data)
        return ModelConfig.model_validate(merged)

    def save(self, config: ModelConfig) -> ModelConfig:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(config.model_dump(), f, ensure_ascii=False, indent=2)
        return config


runtime_config = RuntimeConfigService()
