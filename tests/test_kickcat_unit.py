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
        self.assertIn("memory", state)

    def test_init_keeps_existing_memory_state(self):
        kickcat.init_state(self.state_path)
        now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        payload = {
            "ops": [
                {
                    "type": "memory_sync_upsert",
                    "task_related_items": [
                        {
                            "source_ref": "main_keep",
                            "summary": "Do not reset this memory",
                            "topic": "task",
                            "updated_at": "2026-03-01T09:50:00Z",
                        }
                    ],
                    "non_task_related_items": [],
                }
            ]
        }
        kickcat.apply_ops(self.state_path, payload, now_dt=now)
        kickcat.init_state(self.state_path)
        state = self._read_state()
        self.assertEqual(len(state["memory"]["task_related"]), 1)

    def test_init_creates_from_template_when_state_missing(self):
        template_path = Path(self.tmp.name) / "kickcat.template.json"
        template_state = kickcat.deepcopy(kickcat.DEFAULT_STATE)
        template_state["pet"]["name"] = "TemplateCat"
        template_state["pet"]["hunger"] = 52
        template_path.write_text(
            json.dumps(template_state, ensure_ascii=False), encoding="utf-8"
        )

        out = kickcat.init_state(self.state_path, template_path=template_path)
        self.assertTrue(out["created"])
        state = self._read_state()
        self.assertEqual(state["pet"]["name"], "TemplateCat")
        self.assertEqual(state["pet"]["hunger"], 52)

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

    def test_tick_emits_cat_activity_hint_when_calm(self):
        now = datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc)
        state = kickcat.deepcopy(kickcat.DEFAULT_STATE)
        state["pet"].update({"hunger": 40, "boredom": 50, "happiness": 70})
        self._write_state(state)

        out = kickcat.run_tick(self.state_path, now_dt=now)
        self.assertEqual(out["candidate"], "cat_activity")
        self.assertIn("activity_hint", out)

    def test_tick_cat_activity_hint_respects_cooldown(self):
        now = datetime(2026, 2, 28, 12, 0, tzinfo=timezone.utc)
        state = kickcat.deepcopy(kickcat.DEFAULT_STATE)
        state["pet"].update({"hunger": 40, "boredom": 50, "happiness": 70})
        state["timing"]["last_activity_hint_at"] = iso(now - timedelta(minutes=10))
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
        self.assertIn("memory", summary)

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

    def test_tick_requests_memory_sync_when_due(self):
        now = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        state = kickcat.load_state(self.state_path)
        state["memory"]["last_compact_at"] = iso(now - timedelta(minutes=30))
        kickcat.save_state(self.state_path, state)
        out = kickcat.run_tick(self.state_path, now_dt=now)
        self.assertEqual(out["memory_action"]["type"], "request_memory_sync")
        self.assertEqual(
            out["memory_action"]["task_related_max_bytes"],
            kickcat.TASK_MEMORY_LIMIT_BYTES,
        )
        self.assertEqual(
            out["memory_action"]["non_task_related_max_bytes"],
            kickcat.NON_TASK_MEMORY_LIMIT_BYTES,
        )

    def test_tick_requests_memory_compact_by_size_limit(self):
        now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        state = kickcat.load_state(self.state_path)
        state["memory"]["non_task_related"] = [
            {
                "id": f"mem_{i}",
                "source_ref": f"src_{i}",
                "updated_at": "2026-03-01T10:00:00Z",
                "summary": "x" * 900,
                "topic": "chat",
                "bucket": "non_task_related",
                "reason": "test",
                "synced_at": "2026-03-01T10:00:00Z",
            }
            for i in range(80)
        ]
        kickcat.save_state(self.state_path, state)
        out = kickcat.run_tick(self.state_path, now_dt=now)
        self.assertEqual(out["memory_action"]["type"], "request_memory_compact")
        self.assertEqual(out["memory_action"]["reason"], "size_limit")

    def test_apply_memory_sync_upsert_and_cursor(self):
        now = datetime(2026, 3, 1, 11, 0, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        payload = {
            "ops": [
                {
                    "type": "memory_sync_upsert",
                    "task_related_items": [
                        {
                            "source_ref": "main_100",
                            "summary": "User seems stressed and needs reminder",
                            "topic": "mood",
                            "updated_at": "2026-03-01T10:55:00Z",
                        }
                    ],
                    "non_task_related_items": [],
                    "cursor": {"updated_at": "2026-03-01T10:55:00Z", "id": "main_100"},
                }
            ]
        }
        kickcat.apply_ops(self.state_path, payload, now_dt=now)
        state = self._read_state()
        self.assertEqual(len(state["memory"]["task_related"]), 1)
        self.assertEqual(len(state["memory"]["non_task_related"]), 0)
        self.assertEqual(state["memory"]["last_sync_cursor"]["id"], "main_100")

    def test_apply_memory_compact_replace(self):
        now = datetime(2026, 3, 1, 11, 30, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        payload = {
            "ops": [
                {
                    "type": "memory_compact_replace",
                    "reason": "llm_semantic_compact",
                    "task_related_items": [
                        {
                            "source_ref": "main_100",
                            "summary": "Keep only the key reminder and mood context",
                            "topic": "task",
                            "updated_at": "2026-03-01T11:20:00Z",
                        },
                        {
                            "source_ref": "main_101",
                            "summary": "y" * 9000,
                            "topic": "task",
                            "updated_at": "2026-03-01T11:21:00Z",
                        },
                    ],
                    "non_task_related_items": [
                        {
                            "source_ref": "main_chat_1",
                            "summary": "z" * 6000,
                            "topic": "chat",
                            "updated_at": "2026-03-01T11:22:00Z",
                        }
                    ],
                }
            ]
        }
        kickcat.apply_ops(self.state_path, payload, now_dt=now)
        state = self._read_state()
        task_bytes = state["memory"]["approx_bytes"]["task_related"]
        non_task_bytes = state["memory"]["approx_bytes"]["non_task_related"]
        self.assertLessEqual(task_bytes, kickcat.TASK_MEMORY_LIMIT_BYTES)
        self.assertLessEqual(non_task_bytes, kickcat.NON_TASK_MEMORY_LIMIT_BYTES)
        self.assertEqual(state["memory"]["last_compact_reason"], "llm_semantic_compact")

    def test_preference_upsert_learning(self):
        now = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        payload = {
            "ops": [
                {
                    "type": "preference_upsert",
                    "preferences": {
                        "reply_tone": "gentle",
                        "interaction_style": "brief",
                        "unknown_field": "ignored",
                    },
                }
            ]
        }
        kickcat.apply_ops(self.state_path, payload, now_dt=now)
        state = self._read_state()
        self.assertEqual(state["memory"]["user_preferences"]["reply_tone"], "gentle")
        self.assertNotIn("unknown_field", state["memory"]["user_preferences"])

    def test_cat_command_generates_interaction(self):
        now = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        out = kickcat.run_cat_command(
            self.state_path,
            "喂猫并提醒我 finish report，我今天有点压力",
            now_dt=now,
        )
        self.assertTrue(out["ok"])
        self.assertIn("feed", out["intents"])
        self.assertIn("task_capture", out["intents"])
        self.assertIn("mood_capture", out["intents"])
        state = self._read_state()
        self.assertGreaterEqual(len(state["tasks"]), 1)
        self.assertGreaterEqual(len(state["moods"]), 1)

    def test_cat_command_hides_numeric_summary_without_debug(self):
        now = datetime(2026, 3, 1, 10, 5, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        out = kickcat.run_cat_command(self.state_path, "how are you", now_dt=now)
        self.assertFalse(out["debug_mode"])
        self.assertIn("pet_state", out["summary"])
        self.assertNotIn("pet", out["summary"])
        self.assertNotIn("debug_summary", out)

    def test_cat_command_exposes_debug_summary_for_debug_prefix(self):
        now = datetime(2026, 3, 1, 10, 10, tzinfo=timezone.utc)
        kickcat.init_state(self.state_path)
        out = kickcat.run_cat_command(self.state_path, "DEBUG status", now_dt=now)
        self.assertTrue(out["debug_mode"])
        self.assertIn("debug_summary", out)
        self.assertIn("pet", out["debug_summary"])


if __name__ == "__main__":
    unittest.main()
