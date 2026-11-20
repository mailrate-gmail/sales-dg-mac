import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.main import delete_session_data


class TestDeleteSession(unittest.TestCase):
    def test_delete_session_removes_related_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sessions_dir = base / "sessions"
            audio_dir = base / "audio"
            exports_dir = base / "exports"
            session_id = "20261120_113000"
            sessions_dir.mkdir()
            audio_dir.mkdir()
            exports_dir.mkdir()

            session_file = sessions_dir / f"{session_id}.json"
            memory_file = sessions_dir / f"{session_id}.memory.json"
            export_file = exports_dir / f"{session_id}.md"
            audio_session_dir = audio_dir / session_id
            unknown_dir = sessions_dir / f"{session_id}_unknown_speakers"
            session_file.write_text("{}")
            memory_file.write_text("{}")
            export_file.write_text("# export")
            audio_session_dir.mkdir()
            (audio_session_dir / "message.wav").write_text("audio")
            unknown_dir.mkdir()
            (unknown_dir / "voice.npy").write_text("voice")

            with patch("app.main.storage.sessions_dir", sessions_dir), patch("app.main.storage.audio_dir", audio_dir), patch(
                "app.main.storage.exports_dir", exports_dir
            ):
                delete_session_data(session_id)

            self.assertFalse(session_file.exists())
            self.assertFalse(memory_file.exists())
            self.assertFalse(export_file.exists())
            self.assertFalse(audio_session_dir.exists())
            self.assertFalse(unknown_dir.exists())

    def test_delete_missing_session_raises_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sessions_dir = base / "sessions"
            sessions_dir.mkdir()
            with patch("app.main.storage.sessions_dir", sessions_dir):
                with self.assertRaises(HTTPException) as ctx:
                    delete_session_data("20261120_113000")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
