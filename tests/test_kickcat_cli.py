import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "kickcat" / "kickcat.py"


class KickCatCliTests(unittest.TestCase):
    def test_cli_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "kickcat.json"

            init_out = subprocess.check_output(
                ["python3", str(SCRIPT), "init", "--state-file", str(state_file)],
                text=True,
            )
            init_payload = json.loads(init_out)
            self.assertTrue(init_payload["ok"])

            apply_payload = json.dumps(
                {
                    "ops": [
                        {"type": "touch_interaction"},
                        {"type": "pet_delta", "field": "happiness", "delta": 2},
                        {"type": "task_add", "task": {"title": "finish report"}},
                    ]
                }
            )
            apply_out = subprocess.check_output(
                [
                    "python3",
                    str(SCRIPT),
                    "apply",
                    "--state-file",
                    str(state_file),
                    "--payload",
                    apply_payload,
                ],
                text=True,
            )
            apply_json = json.loads(apply_out)
            self.assertEqual(apply_json["applied_ops"], 3)

            tick_out = subprocess.check_output(
                ["python3", str(SCRIPT), "tick", "--state-file", str(state_file)],
                text=True,
            )
            tick_json = json.loads(tick_out)
            self.assertTrue(tick_json["ok"])
            self.assertIn("memory_action", tick_json)

            summary_out = subprocess.check_output(
                ["python3", str(SCRIPT), "summary", "--state-file", str(state_file)],
                text=True,
            )
            summary_json = json.loads(summary_out)
            self.assertIn("pet", summary_json)

    def test_cli_cat_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "kickcat.json"
            subprocess.check_output(
                ["python3", str(SCRIPT), "init", "--state-file", str(state_file)],
                text=True,
            )
            out = subprocess.check_output(
                [
                    "python3",
                    str(SCRIPT),
                    "cat",
                    "--state-file",
                    str(state_file),
                    "--text",
                    "feed cat and remind me to write report",
                ],
                text=True,
            )
            payload = json.loads(out)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["mode"], "cat")
            self.assertIn("feed", payload["intents"])

    def test_cli_debug_mode_returns_json_on_error_and_writes_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "missing.json"
            debug_log = Path(tmp) / "kickcat-debug.jsonl"

            proc = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "summary",
                    "--state-file",
                    str(state_file),
                    "--mode",
                    "debug",
                    "--debug-log-file",
                    str(debug_log),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["mode"], "debug")
            self.assertEqual(payload["fallback"], "HEARTBEAT_OK")
            self.assertTrue(payload["debug_log_written"])
            self.assertTrue(debug_log.exists())

    def test_cli_deploy_mode_returns_nonzero_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "missing.json"

            proc = subprocess.run(
                ["python3", str(SCRIPT), "summary", "--state-file", str(state_file)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            payload = json.loads(proc.stdout)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["mode"], "deploy")


if __name__ == "__main__":
    unittest.main()
