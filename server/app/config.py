from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    data_dir: str = "/app/data"

    whisper_model: str = "small"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    asr_provider: str = "funasr_streaming"
    funasr_model: str = "paraformer-zh-streaming"
    funasr_device: str = "cpu"
    oci_speech_region: str = ""
    oci_speech_compartment_id: str = ""
    oci_speech_language: str = "zh-CN"
    oci_speech_profile: str = "DEFAULT"
    oci_speech_config_file: str = "/root/.oci/config"
    oci_speech_realtime_endpoint: str = ""

    speaker_threshold: float = 0.30
    speaker_cluster_threshold: float = 0.45
    speaker_diarization_enabled: bool = False
    energy_threshold: float = 0.008
    silence_duration_ms: int = 800
    min_speech_duration_ms: int = 1000
    punctuation_sensitivity: float = 0.6
    punctuation_provider: str = "funasr_punc"
    punctuation_model: str = "ct-punc"
    punctuation_device: str = "cpu"

    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_timeout_seconds: int = 30

    class Config:
        env_file = ".env"


settings = Settings()
