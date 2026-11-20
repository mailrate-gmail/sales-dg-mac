import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.main import create_message_from_external_transcript, resolve_external_speaker_fields
from app.models import ExternalTranscriptCreate, Participant, Session


class TestExternalTranscript(unittest.IsolatedAsyncioTestCase):
    def test_participant_id_uses_registered_speaker_config(self):
        participant = Participant(
            id="participant_1",
            name="张总",
            role="customer",
            need_reply=True,
        )
        payload = ExternalTranscriptCreate(
            text="是否支持私有化部署？",
            participant_id="participant_1",
            speaker_name="客户端名称会被忽略",
            role="unknown",
            need_reply=False,
        )

        with patch("app.main.load_participants", return_value=[participant]):
            fields = resolve_external_speaker_fields(payload)

        self.assertEqual(fields["participant_id"], "participant_1")
        self.assertEqual(fields["speaker_name"], "张总")
        self.assertEqual(fields["role"], "customer")
        self.assertTrue(fields["need_reply"])

    async def test_manual_speaker_writes_text_without_audio_or_llm_by_default(self):
        session = Session(id="session_1", title="测试会谈", goal="测试", started_at="2026-11-20T10:00:00")
        payload = ExternalTranscriptCreate(
            text="客户说明了当前情况。",
            speaker_name="客户",
            role="customer",
            source="macos_speech_asr",
        )

        with patch("app.main.load_participants", return_value=[]), patch("app.main.save_session") as save_session, patch(
            "app.main.context_manager.update_with_message"
        ) as update_memory, patch("app.main.llm_client.suggest_reply", new_callable=AsyncMock) as suggest_reply:
            message = await create_message_from_external_transcript(session, payload)

        self.assertEqual(message.text, "客户说明了当前情况。")
        self.assertEqual(message.speaker_name, "客户")
        self.assertEqual(message.role, "customer")
        self.assertIsNone(message.audio_file)
        self.assertIsNone(message.speaker_score)
        self.assertEqual(message.source, "macos_speech_asr")
        self.assertFalse(message.need_reply)
        suggest_reply.assert_not_called()
        save_session.assert_called_once()
        update_memory.assert_called_once()

    async def test_empty_text_raises_400(self):
        session = Session(id="session_1", started_at="2026-11-20T10:00:00")
        payload = ExternalTranscriptCreate(text="   ")
        with self.assertRaises(HTTPException) as ctx:
            await create_message_from_external_transcript(session, payload)
        self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
