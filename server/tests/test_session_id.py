import unittest
from datetime import datetime as real_datetime
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.main import create_session_from_title_goal


class FixedDatetime:
    @staticmethod
    def now():
        return real_datetime(2026, 11, 20, 11, 15, 30)


class TestSessionId(unittest.TestCase):
    def test_session_id_uses_date_time_seconds(self):
        session_path = Mock()
        session_path.exists.return_value = False
        with patch("app.main.datetime", FixedDatetime), patch("app.main.storage.session_file", return_value=session_path), patch(
            "app.main.save_session"
        ):
            session = create_session_from_title_goal("测试会谈", "测试目标")

        self.assertEqual(session.id, "20261120_111530")
        self.assertEqual(session.started_at, "2026-11-20T11:15:30")

    def test_session_id_collision_returns_409(self):
        session_path = Mock()
        session_path.exists.return_value = True
        with patch("app.main.datetime", FixedDatetime), patch("app.main.storage.session_file", return_value=session_path):
            with self.assertRaises(HTTPException) as ctx:
                create_session_from_title_goal("测试会谈", "测试目标")

        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
