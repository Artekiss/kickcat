import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from skills.kickcat import kickcat


def iso(dt):
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


class KickCatUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.tmp.name) / "kickcat.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_state(self, state):
        with self.state_path.open("w", encoding="utf-8") as f:
            json.dump(state, f)

    def _read_state(self):
        with self.state_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def test_init_creates_and_is_idempotent(self):
        first = kickcat.init_state(self.state_path)
        self.assertTrue(first["created"])
        second = kickcat.init_state(self.state_path)
        self.assertFalse(second["created"])
        state = self._read_state()
        self.assertEqual(state["pet"]["name"], "KickCat")

    def test_tick_progression_with_recent_interaction_and_penalties(self):
        now = datetime(2026, 2, 28, 10, 30, tzinfo=timezone.utc)
        state = kickcat.deepcopy(kickcat.DEFAULT_STATE)
        state["pet"].update({"hunger": 79, "happiness": 50, "boredom": 74})
        state["timing"]["last_tick_at"] = iso(now - timedelta(minutes=20))
        state["timing"]["last_interaction_at"] = iso(now - timedelta(minutes=30))
        self._write_state(state)

        out = kickcat.run_tick(self.state_path, now_dt=now)
        self.assertEqual(out["ticks_applied"], 2)
        updated = self._read_state()
        self.assertEqual(
            updated["pet"],
            {"name": "KickCat", "hunger": 87, "happiness": 40, "boredom": 76},
        )

    def test_tick_prefers_task_reminder(self):
        now = datetime(2026, 2, 28, 10, 0, tzinfo=timezone.utc)
        state = kickcat.deepcopy(kickcat.DEFAULT_STATE)
        state["pet"].update({"hunger": 90, "boredom": 90})
        state["tasks"].append(
            {
                "id": "task_001",
                "title": "finish report",
                "created_at": iso(now - timedelta(hours=1)),
                "last_reminded_at": None,
                "requested_remind_at": iso(now - timedelta(minutes=1)),
            }
        )
        self._write_state(state)

        out = kickcat.run_tick(self.state_path, now_dt=now)
        self.assertEqual(out["candidate"], "task_reminder")
        updated = self._read_state()
        self.assertEqual(updated["tasks"][0]["last_reminded_at"], iso(now))

    def test_tick_random_ping_cooldown_to_none(self):
        now = datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc)
        state = kickcat.deepcopy(kickcat.DEFAULT_STATE)
        state["pet"].update({"hunger": 85, "boredom": 80})
        state["timing"]["last_random_ping_at"] = iso(now - timedelta(minutes=10))
        self._write_state(state)

        out = kickcat.run_tick(self.state_path, now_dt=now)
        self.assertEqual(out["candidate"], "none")

    def test_apply_pet_delta_validation_and_clamp(self):
        kickcat.init_state(self.state_path)
        payload = {"ops": [{"type": "pet_delta", "field": "hunger", "delta": 200}]}
        with self.assertRaises(ValueError):
            kickcat.apply_ops(self.state_path, payload)

        payload2 = {"ops": [{"type": "pet_delta", "field": "happiness", "delta": 30}]}
        kickcat.apply_ops(self.state_path, payload2)
        state = self._read_state()
        self.assertEqual(state["pet"]["happiness"], 95)

    def test_apply_feed_strength_cooldown_and_message_dedupe(self):
        base = datetime(2026, 2, 28, 9, 0, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)

        first = {
            "message_id": "msg-1",
            "ops": [{"type": "pet_delta", "field": "hunger", "feed_strength": 1.0}],
        }
        out1 = kickcat.apply_ops(self.state_path, first, now_dt=base)
        self.assertEqual(out1["pet"]["hunger"], 20)

        second = {
            "message_id": "msg-2",
            "ops": [{"type": "pet_delta", "field": "hunger", "feed_strength": 0.5}],
        }
        out2 = kickcat.apply_ops(
            self.state_path, second, now_dt=base + timedelta(minutes=2)
        )
        self.assertEqual(out2["skipped_ops"][0]["reason"], "feed_cooldown")

        third = {
            "message_id": "msg-1",
            "ops": [{"type": "pet_delta", "field": "hunger", "feed_strength": 0.8}],
        }
        out3 = kickcat.apply_ops(
            self.state_path, third, now_dt=base + timedelta(minutes=6)
        )
        self.assertEqual(out3["skipped_ops"][0]["reason"], "duplicate_message_feed")

    def test_apply_task_add_autofill_and_time_validation(self):
        now = datetime(2026, 2, 28, 8, 0, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        payload = {
            "ops": [
                {
                    "type": "task_add",
                    "task": {
                        "title": "write daily report",
                        "due_at": "2026-02-28T18:00:00Z",
                    },
                }
            ]
        }
        kickcat.apply_ops(self.state_path, payload, now_dt=now)
        state = self._read_state()
        self.assertEqual(state["tasks"][0]["id"], "task_001")
        self.assertEqual(state["tasks"][0]["created_at"], iso(now))

        bad_payload = {
            "ops": [
                {
                    "type": "task_add",
                    "task": {
                        "title": "bad time",
                        "due_at": "2026-02-28 18:00:00",
                    },
                }
            ]
        }
        with self.assertRaises(ValueError):
            kickcat.apply_ops(self.state_path, bad_payload, now_dt=now)

    def test_apply_mood_add_validation_and_autofill(self):
        now = datetime(2026, 2, 28, 8, 30, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        payload = {
            "ops": [
                {
                    "type": "mood_add",
                    "mood": {"label": "stressed", "valence": -0.6, "intensity": 0.7},
                }
            ]
        }
        kickcat.apply_ops(self.state_path, payload, now_dt=now)
        state = self._read_state()
        self.assertEqual(state["moods"][0]["id"], "mood_001")
        self.assertEqual(state["moods"][0]["created_at"], iso(now))

        bad = {
            "ops": [
                {
                    "type": "mood_add",
                    "mood": {"label": "oops", "valence": -2.0, "intensity": 0.4},
                }
            ]
        }
        with self.assertRaises(ValueError):
            kickcat.apply_ops(self.state_path, bad, now_dt=now)

    def test_touch_interaction_updates_utc_timestamp(self):
        now = datetime(2026, 2, 28, 11, 11, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        payload = {"ops": [{"type": "touch_interaction"}]}
        kickcat.apply_ops(self.state_path, payload, now_dt=now)
        state = self._read_state()
        self.assertEqual(state["timing"]["last_interaction_at"], iso(now))

    def test_summary_shape(self):
        kickcat.init_state(self.state_path)
        summary = kickcat.build_summary(self.state_path)
        self.assertIn("pet", summary)
        self.assertIn("candidate", summary)
        self.assertIn("task_hint", summary)
        self.assertIn("mood_hint", summary)

    def test_resolve_mode_from_env(self):
        with mock.patch.dict(
            "os.environ", {kickcat.MODE_ENV_KEY: "debug"}, clear=False
        ):
            self.assertEqual(kickcat.resolve_mode(None), "debug")

    def test_resolve_mode_invalid_raises(self):
        with self.assertRaises(ValueError):
            kickcat.resolve_mode("oops")

    def test_write_debug_log_jsonl(self):
        log_path = Path(self.tmp.name) / "debug.jsonl"
        payload = {"ok": False, "message": "boom"}
        kickcat.write_debug_log(log_path, payload)
        text = log_path.read_text(encoding="utf-8").strip()
        self.assertEqual(json.loads(text)["message"], "boom")


if __name__ == "__main__":
    unittest.main()
