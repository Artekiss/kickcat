#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import sys
import traceback
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_STATE_PATH = ROOT_DIR / "data" / "kickcat.json"

TICK_MINUTES = 10
FEED_COOLDOWN_MINUTES = 5
TASK_REMIND_COOLDOWN_MINUTES = 30
RANDOM_PING_COOLDOWN_MINUTES = 60
MAX_PET_DELTA = 30
DEFAULT_MODE = "deploy"
DEBUG_MODE = "debug"
MODE_ENV_KEY = "KICKCAT_MODE"
DEBUG_LOG_ENV_KEY = "KICKCAT_DEBUG_LOG_FILE"
DEFAULT_DEBUG_LOG = "logs/kickcat-debug.jsonl"


DEFAULT_STATE = {
    "version": 1,
    "pet": {
        "name": "KickCat",
        "hunger": 35,
        "happiness": 65,
        "boredom": 45,
    },
    "timing": {
        "last_tick_at": None,
        "last_feed_at": None,
        "last_interaction_at": None,
        "last_random_ping_at": None,
    },
    "tasks": [],
    "moods": [],
    "meta": {
        "last_action_candidate": "none",
        "last_action_reason": None,
        "last_message_hash": None,
    },
}


def now_utc():
    return datetime.now(timezone.utc)


def to_iso_utc(dt):
    return (
        dt.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def parse_iso_utc(value, field_name):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO 8601 UTC string")
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include timezone")
    return parsed.astimezone(timezone.utc)


def clamp(num, low, high):
    return max(low, min(high, num))


def clamp_pet(state):
    for field in ("hunger", "happiness", "boredom"):
        state["pet"][field] = int(clamp(state["pet"][field], 0, 100))


def load_state(state_path):
    path = Path(state_path)
    if not path.exists():
        raise FileNotFoundError(f"State file does not exist: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state_path, state):
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def init_state(state_path):
    path = Path(state_path)
    if path.exists():
        state = load_state(path)
        return {
            "ok": True,
            "created": False,
            "state_path": str(path),
            "pet": state["pet"],
        }
    state = deepcopy(DEFAULT_STATE)
    save_state(path, state)
    return {"ok": True, "created": True, "state_path": str(path), "pet": state["pet"]}


def _minutes_since(current_dt, prev_iso):
    if not prev_iso:
        return None
    prev = parse_iso_utc(prev_iso, "timestamp")
    return (current_dt - prev).total_seconds() / 60.0


def _is_recent_interaction(state, current_dt):
    delta = _minutes_since(current_dt, state["timing"].get("last_interaction_at"))
    return delta is not None and delta <= 60


def _gen_id(prefix, items):
    max_num = 0
    for item in items:
        value = item.get("id", "")
        if isinstance(value, str) and value.startswith(prefix + "_"):
            tail = value.split("_", 1)[1]
            if tail.isdigit():
                max_num = max(max_num, int(tail))
    return f"{prefix}_{max_num + 1:03d}"


def _task_eligible(task, current_dt):
    last_reminded = parse_iso_utc(task.get("last_reminded_at"), "last_reminded_at")
    if last_reminded is not None:
        if (
            current_dt - last_reminded
        ).total_seconds() < TASK_REMIND_COOLDOWN_MINUTES * 60:
            return False, None

    requested = parse_iso_utc(task.get("requested_remind_at"), "requested_remind_at")
    due = parse_iso_utc(task.get("due_at"), "due_at")

    if requested is not None and current_dt >= requested:
        return True, "requested_remind_at"

    if requested is None and due is not None:
        minutes_to_due = (due - current_dt).total_seconds() / 60.0
        if 0 <= minutes_to_due <= 60:
            return True, "due_soon"
        if -120 <= minutes_to_due < 0 and last_reminded is None:
            return True, "due_overdue"

    return False, None


def _pick_task_reminder(state, current_dt):
    for idx, task in enumerate(state["tasks"]):
        eligible, reason = _task_eligible(task, current_dt)
        if eligible:
            return idx, task, reason
    return None, None, None


def _pick_random_ping(state, current_dt):
    last_random = state["timing"].get("last_random_ping_at")
    if last_random is not None:
        minutes = _minutes_since(current_dt, last_random)
        if minutes is not None and minutes < RANDOM_PING_COOLDOWN_MINUTES:
            return "none", None

    hunger = state["pet"]["hunger"]
    boredom = state["pet"]["boredom"]

    if hunger < 75 and boredom < 70:
        return "none", None
    if hunger >= 80 and boredom >= 70:
        return "gentle_checkin", "high_hunger_and_boredom"
    if hunger >= boredom and hunger >= 75:
        return "hungry_ping", "hunger_high"
    if boredom >= 70:
        return "playful_ping", "boredom_high"
    return "gentle_checkin", "state_checkin"


def choose_action_candidate(state, current_dt):
    task_idx, task, task_reason = _pick_task_reminder(state, current_dt)
    if task is not None:
        state["tasks"][task_idx]["last_reminded_at"] = to_iso_utc(current_dt)
        return "task_reminder", f"task:{task_reason}", task

    action, reason = _pick_random_ping(state, current_dt)
    if action != "none":
        state["timing"]["last_random_ping_at"] = to_iso_utc(current_dt)
        return action, reason, None

    return "none", "no_action_needed", None


def run_tick(state_path, now_dt=None):
    state = load_state(state_path)
    current_dt = now_dt or now_utc()

    last_tick_iso = state["timing"].get("last_tick_at")
    if last_tick_iso is None:
        tick_count = 1
    else:
        last_tick_dt = parse_iso_utc(last_tick_iso, "last_tick_at")
        if last_tick_dt is None:
            tick_count = 1
            last_tick_dt = current_dt
        elapsed_seconds = (current_dt - last_tick_dt).total_seconds()
        tick_count = int(max(0, math.floor(elapsed_seconds / (TICK_MINUTES * 60))))

    recent_interaction = _is_recent_interaction(state, current_dt)
    for _ in range(tick_count):
        state["pet"]["hunger"] += 4
        state["pet"]["boredom"] += 3
        if recent_interaction:
            state["pet"]["boredom"] -= 2
        if state["pet"]["hunger"] >= 80:
            state["pet"]["happiness"] -= 3
        if state["pet"]["boredom"] >= 75:
            state["pet"]["happiness"] -= 2
        clamp_pet(state)

    candidate, reason, task = choose_action_candidate(state, current_dt)
    state["meta"]["last_action_candidate"] = candidate
    state["meta"]["last_action_reason"] = reason
    state["timing"]["last_tick_at"] = to_iso_utc(current_dt)
    save_state(state_path, state)

    out = {
        "ok": True,
        "ticks_applied": tick_count,
        "pet": state["pet"],
        "candidate": candidate,
        "reason": reason,
    }
    if task is not None:
        out["task_hint"] = task.get("title")
    return out


def _normalize_task_candidate(task_obj):
    if not isinstance(task_obj, dict):
        raise ValueError("task must be an object")
    title = task_obj.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("task.title is required")
    due_at = task_obj.get("due_at")
    requested = task_obj.get("requested_remind_at")
    if due_at is not None:
        parse_iso_utc(due_at, "due_at")
    if requested is not None:
        parse_iso_utc(requested, "requested_remind_at")
    cleaned = {
        "title": title.strip(),
        "due_at": due_at,
        "requested_remind_at": requested,
    }
    return cleaned


def _normalize_mood_candidate(mood_obj):
    if not isinstance(mood_obj, dict):
        raise ValueError("mood must be an object")
    label = mood_obj.get("label")
    valence = mood_obj.get("valence")
    intensity = mood_obj.get("intensity")
    if not isinstance(label, str) or not label.strip():
        raise ValueError("mood.label is required")
    if not isinstance(valence, (int, float)) or not -1.0 <= float(valence) <= 1.0:
        raise ValueError("mood.valence must be in -1.0..1.0")
    if not isinstance(intensity, (int, float)) or not 0.0 <= float(intensity) <= 1.0:
        raise ValueError("mood.intensity must be in 0.0..1.0")
    return {
        "label": label.strip(),
        "valence": float(valence),
        "intensity": float(intensity),
    }


def map_feed_strength(feed_strength):
    strength = clamp(float(feed_strength), 0.0, 1.0)
    return int(round(8 + 7 * strength))


def _hash_message(payload):
    message_id = payload.get("message_id")
    message_text = payload.get("message_text")
    if message_id is None and message_text is None:
        return None
    base = str(message_id if message_id is not None else message_text)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def apply_ops(state_path, payload, now_dt=None):
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    ops = payload.get("ops")
    if not isinstance(ops, list):
        raise ValueError("payload.ops must be a list")

    state = load_state(state_path)
    current_dt = now_dt or now_utc()
    now_iso = to_iso_utc(current_dt)
    message_hash = _hash_message(payload)

    applied_count = 0
    skipped = []

    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise ValueError(f"op[{index}] must be an object")
        op_type = op.get("type")
        if op_type == "pet_delta":
            field = op.get("field")
            if field not in ("hunger", "happiness", "boredom"):
                raise ValueError("pet_delta.field must be hunger|happiness|boredom")

            if "feed_strength" in op:
                if field != "hunger":
                    raise ValueError("feed_strength only supports hunger field")
                delta = -map_feed_strength(op["feed_strength"])
            else:
                delta = op.get("delta")
                if not isinstance(delta, (int, float)):
                    raise ValueError("pet_delta.delta must be a number")
                delta = int(delta)

            if abs(delta) > MAX_PET_DELTA:
                raise ValueError(f"pet_delta.delta out of range: +/-{MAX_PET_DELTA}")

            is_feed = field == "hunger" and delta < 0
            if is_feed:
                last_feed = parse_iso_utc(
                    state["timing"].get("last_feed_at"), "last_feed_at"
                )
                if message_hash and message_hash == state["meta"].get(
                    "last_message_hash"
                ):
                    skipped.append({"op": index, "reason": "duplicate_message_feed"})
                    continue
                if last_feed and (current_dt - last_feed) < timedelta(
                    minutes=FEED_COOLDOWN_MINUTES
                ):
                    skipped.append({"op": index, "reason": "feed_cooldown"})
                    continue

            state["pet"][field] += delta
            clamp_pet(state)
            applied_count += 1

            if is_feed:
                state["timing"]["last_feed_at"] = now_iso
                if message_hash:
                    state["meta"]["last_message_hash"] = message_hash

        elif op_type == "task_add":
            task_obj = _normalize_task_candidate(op.get("task"))
            dedupe_key = (
                task_obj["title"].lower(),
                task_obj.get("due_at"),
                task_obj.get("requested_remind_at"),
            )
            duplicated = False
            for existing in state["tasks"]:
                existing_key = (
                    str(existing.get("title", "")).lower(),
                    existing.get("due_at"),
                    existing.get("requested_remind_at"),
                )
                if existing_key == dedupe_key:
                    duplicated = True
                    break
            if duplicated:
                skipped.append({"op": index, "reason": "task_duplicate"})
                continue

            new_task = {
                "id": _gen_id("task", state["tasks"]),
                "title": task_obj["title"],
                "created_at": now_iso,
                "last_reminded_at": None,
            }
            if task_obj.get("due_at") is not None:
                new_task["due_at"] = task_obj["due_at"]
            if task_obj.get("requested_remind_at") is not None:
                new_task["requested_remind_at"] = task_obj["requested_remind_at"]
            state["tasks"].append(new_task)
            applied_count += 1

        elif op_type == "mood_add":
            mood_obj = _normalize_mood_candidate(op.get("mood"))
            new_mood = {
                "id": _gen_id("mood", state["moods"]),
                "label": mood_obj["label"],
                "valence": mood_obj["valence"],
                "intensity": mood_obj["intensity"],
                "created_at": now_iso,
            }
            state["moods"].append(new_mood)
            applied_count += 1

        elif op_type == "touch_interaction":
            state["timing"]["last_interaction_at"] = now_iso
            applied_count += 1

        else:
            raise ValueError(f"unsupported op type: {op_type}")

    clamp_pet(state)
    save_state(state_path, state)

    return {
        "ok": True,
        "applied_ops": applied_count,
        "skipped_ops": skipped,
        "pet": state["pet"],
        "tasks_count": len(state["tasks"]),
        "moods_count": len(state["moods"]),
    }


def build_summary(state_path):
    state = load_state(state_path)
    latest_task = state["tasks"][-1]["title"] if state["tasks"] else None
    latest_mood = state["moods"][-1]["label"] if state["moods"] else None
    return {
        "ok": True,
        "pet": {
            "hunger": state["pet"]["hunger"],
            "happiness": state["pet"]["happiness"],
            "boredom": state["pet"]["boredom"],
        },
        "candidate": state["meta"].get("last_action_candidate", "none"),
        "task_hint": latest_task,
        "mood_hint": latest_mood,
    }


def _load_payload(args):
    if args.payload and args.payload_file:
        raise ValueError("Use either --payload or --payload-file")
    if args.payload:
        return json.loads(args.payload)
    if args.payload_file:
        with Path(args.payload_file).open("r", encoding="utf-8") as f:
            return json.load(f)
    raise ValueError("apply requires --payload or --payload-file")


def resolve_mode(cli_mode):
    mode = cli_mode or os.getenv(MODE_ENV_KEY, DEFAULT_MODE)
    if mode not in (DEFAULT_MODE, DEBUG_MODE):
        raise ValueError(f"mode must be {DEFAULT_MODE}|{DEBUG_MODE}")
    return mode


def resolve_debug_log_file(cli_path):
    path_str = cli_path or os.getenv(DEBUG_LOG_ENV_KEY, DEFAULT_DEBUG_LOG)
    path = Path(path_str)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def write_debug_log(log_file, payload):
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def error_payload(exc, args, mode):
    return {
        "ok": False,
        "mode": mode,
        "command": getattr(args, "command", None),
        "fallback": "HEARTBEAT_OK",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
        },
        "ts": to_iso_utc(now_utc()),
    }


def execute_command(args):
    if args.command == "init":
        return init_state(args.state_file)
    if args.command == "tick":
        return run_tick(args.state_file)
    if args.command == "apply":
        payload = _load_payload(args)
        return apply_ops(args.state_file, payload)
    return build_summary(args.state_file)


def _add_runtime_flags(parser):
    parser.add_argument("--mode", choices=(DEFAULT_MODE, DEBUG_MODE), default=None)
    parser.add_argument("--debug-log-file", default=None)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="KickCat v1 local state script")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for cmd in ("init", "tick", "summary"):
        p = subparsers.add_parser(cmd)
        p.add_argument("--state-file", default=str(DEFAULT_STATE_PATH))
        _add_runtime_flags(p)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--state-file", default=str(DEFAULT_STATE_PATH))
    apply_parser.add_argument("--payload")
    apply_parser.add_argument("--payload-file")
    _add_runtime_flags(apply_parser)
    return parser.parse_args(argv)


def emit_json(payload):
    print(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )


def safe_main(argv=None):
    args = parse_args(argv)
    mode = resolve_mode(getattr(args, "mode", None))
    debug_log_file = resolve_debug_log_file(getattr(args, "debug_log_file", None))
    try:
        result = execute_command(args)
        emit_json(result)
        return 0
    except Exception as exc:
        payload = error_payload(exc, args, mode)
        if mode == DEBUG_MODE:
            payload["debug_log_file"] = str(debug_log_file)
            payload["traceback"] = traceback.format_exc()
            try:
                write_debug_log(debug_log_file, payload)
                payload["debug_log_written"] = True
            except Exception as log_exc:
                payload["debug_log_written"] = False
                payload["debug_log_error"] = str(log_exc)
            emit_json(payload)
            return 0

        emit_json(payload)
        return 1


if __name__ == "__main__":
    sys.exit(safe_main())
