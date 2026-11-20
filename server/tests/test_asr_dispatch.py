import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.services.asr_dispatch import transcribe_audio_file


class TestAsrDispatch(unittest.TestCase):
    def test_faster_whisper_uses_asr_service(self):
        with patch("app.services.asr_dispatch.runtime_config.get") as cfg, patch(
            "app.services.asr_dispatch._transcribe_with_faster_whisper"
        ) as tr:
            cfg.return_value.asr_provider = "faster_whisper"
            cfg.return_value.bilingual_asr_enabled = False
            tr.return_value = [{"text": "你好", "start": 0.0, "end": 1.0}]
            result = transcribe_audio_file("/tmp/a.wav")
        self.assertEqual(result, [{"text": "你好", "start": 0.0, "end": 1.0}])
        tr.assert_called_once_with("/tmp/a.wav", language="zh")

    def test_faster_whisper_bilingual_uses_auto_language(self):
        with patch("app.services.asr_dispatch.runtime_config.get") as cfg, patch(
            "app.services.asr_dispatch._transcribe_with_faster_whisper"
        ) as tr:
            cfg.return_value.asr_provider = "faster_whisper"
            cfg.return_value.bilingual_asr_enabled = True
            tr.return_value = [{"text": "Hello 你好", "start": 0.0, "end": 1.0}]
            result = transcribe_audio_file("/tmp/a.wav")
        self.assertEqual(result, [{"text": "Hello 你好", "start": 0.0, "end": 1.0}])
        tr.assert_called_once_with("/tmp/a.wav", language=None)

    def test_funasr_streaming_returns_single_segment(self):
        with patch("app.services.asr_dispatch.runtime_config.get") as cfg, patch(
            "app.services.asr_dispatch._transcribe_with_funasr"
        ) as tr:
            cfg.return_value.asr_provider = "funasr_streaming"
            cfg.return_value.bilingual_asr_enabled = False
            tr.return_value = [{"text": "你好世界", "start": 0.0, "end": 0.0}]
            result = transcribe_audio_file("/tmp/a.wav")
        self.assertEqual(result, [{"text": "你好世界", "start": 0.0, "end": 0.0}])
        tr.assert_called_once_with("/tmp/a.wav")

    def test_funasr_streaming_bilingual_prefers_whisper(self):
        with patch("app.services.asr_dispatch.runtime_config.get") as cfg, patch(
            "app.services.asr_dispatch._transcribe_with_faster_whisper"
        ) as whisper, patch("app.services.asr_dispatch._transcribe_with_funasr") as funasr:
            cfg.return_value.asr_provider = "funasr_streaming"
            cfg.return_value.bilingual_asr_enabled = True
            whisper.return_value = [{"text": "Hello 你好", "start": 0.0, "end": 1.0}]
            result = transcribe_audio_file("/tmp/a.wav")
        self.assertEqual(result, [{"text": "Hello 你好", "start": 0.0, "end": 1.0}])
        whisper.assert_called_once_with("/tmp/a.wav", language=None)
        funasr.assert_not_called()

    def test_funasr_streaming_bilingual_falls_back_to_funasr(self):
        with patch("app.services.asr_dispatch.runtime_config.get") as cfg, patch(
            "app.services.asr_dispatch._transcribe_with_faster_whisper"
        ) as whisper, patch("app.services.asr_dispatch._transcribe_with_funasr") as funasr:
            cfg.return_value.asr_provider = "funasr_streaming"
            cfg.return_value.bilingual_asr_enabled = True
            whisper.side_effect = RuntimeError("missing model")
            funasr.return_value = [{"text": "你好世界", "start": 0.0, "end": 0.0}]
            result = transcribe_audio_file("/tmp/a.wav")
        self.assertEqual(result, [{"text": "你好世界", "start": 0.0, "end": 0.0}])
        funasr.assert_called_once_with("/tmp/a.wav")

    def test_oci_speech_raises_501(self):
        with patch("app.services.asr_dispatch.runtime_config.get") as cfg:
            cfg.return_value.asr_provider = "oci_speech"
            cfg.return_value.bilingual_asr_enabled = False
            with self.assertRaises(HTTPException) as ctx:
                transcribe_audio_file("/tmp/a.wav")
        self.assertEqual(ctx.exception.status_code, 501)

    def test_unknown_provider_raises_400(self):
        with patch("app.services.asr_dispatch.runtime_config.get") as cfg:
            cfg.return_value.asr_provider = "unknown"
            cfg.return_value.bilingual_asr_enabled = False
            with self.assertRaises(HTTPException) as ctx:
                transcribe_audio_file("/tmp/a.wav")
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
