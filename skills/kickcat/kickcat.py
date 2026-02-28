#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import re
import sys
import traceback
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_TEMPLATE_STATE_PATH = ROOT_DIR / "data" / "kickcat.json"
DEFAULT_STATE_PATH = ROOT_DIR / "data" / "kickcat-running.json"

TICK_MINUTES = 10
FEED_COOLDOWN_MINUTES = 5
TASK_REMIND_COOLDOWN_MINUTES = 30
RANDOM_PING_COOLDOWN_MINUTES = 60
ACTIVITY_HINT_COOLDOWN_MINUTES = 45
MAX_PET_DELTA = 30
MEMORY_SYNC_INTERVAL_MINUTES = 180
MEMORY_COMPACT_THRESHOLD_BYTES = 32 * 1024
MEMORY_COMPACT_HOUR_LOCAL = 3
MEMORY_COMPACT_KEEP_ITEMS = 60
TASK_MEMORY_LIMIT_BYTES = 8 * 1024
NON_TASK_MEMORY_LIMIT_BYTES = 4 * 1024
DEFAULT_MODE = "deploy"
DEBUG_MODE = "debug"
MODE_ENV_KEY = "KICKCAT_MODE"
DEBUG_LOG_ENV_KEY = "KICKCAT_DEBUG_LOG_FILE"
DEFAULT_DEBUG_LOG = "logs/kickcat-debug.jsonl"


DEFAULT_STATE = {
    "version": 2,
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
        "last_activity_hint_at": None,
    },
    "tasks": [],
    "moods": [],
    "meta": {
        "last_action_candidate": "none",
        "last_action_reason": None,
        "last_message_hash": None,
    },
    "memory": {
        "task_related": [],
        "non_task_related": [],
        "user_preferences": {},
        "last_sync_at": None,
        "last_sync_cursor": {"updated_at": None, "id": None},
        "last_compact_at": None,
        "last_compact_reason": None,
        "approx_bytes": {"task_related": 0, "non_task_related": 0, "total": 0},
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


def _default_memory_state():
    return deepcopy(DEFAULT_STATE["memory"])


def ensure_state_schema(state):
    merged = deepcopy(DEFAULT_STATE)
    merged.update(state)

    merged["pet"] = {**DEFAULT_STATE["pet"], **state.get("pet", {})}
    merged["timing"] = {**DEFAULT_STATE["timing"], **state.get("timing", {})}
    merged["meta"] = {**DEFAULT_STATE["meta"], **state.get("meta", {})}
    merged["memory"] = _default_memory_state()
    merged["memory"].update(state.get("memory", {}))
    if isinstance(state.get("memory", {}).get("items"), list):
        merged["memory"]["non_task_related"] = state["memory"].get("items", [])
    if not isinstance(merged["memory"].get("task_related"), list):
        merged["memory"]["task_related"] = []
    if not isinstance(merged["memory"].get("non_task_related"), list):
        merged["memory"]["non_task_related"] = []
    if not isinstance(merged["memory"].get("user_preferences"), dict):
        merged["memory"]["user_preferences"] = {}
    if not isinstance(merged["memory"].get("last_sync_cursor"), dict):
        merged["memory"]["last_sync_cursor"] = {"updated_at": None, "id": None}
    merged["memory"]["last_sync_cursor"] = {
        "updated_at": merged["memory"]["last_sync_cursor"].get("updated_at"),
        "id": merged["memory"]["last_sync_cursor"].get("id"),
    }

    merged["tasks"] = (
        state.get("tasks", []) if isinstance(state.get("tasks", []), list) else []
    )
    merged["moods"] = (
        state.get("moods", []) if isinstance(state.get("moods", []), list) else []
    )
    merged["version"] = 2
    clamp_pet(merged)
    _refresh_memory_stats(merged["memory"])
    return merged


def _estimate_memory_bucket_bytes(items):
    return len(json.dumps(items, ensure_ascii=False).encode("utf-8"))


def estimate_memory_bytes(memory_state):
    stats = _memory_stats(memory_state)
    return stats["total"]


def _memory_stats(memory_state):
    task_bytes = _estimate_memory_bucket_bytes(memory_state.get("task_related", []))
    non_task_bytes = _estimate_memory_bucket_bytes(
        memory_state.get("non_task_related", [])
    )
    return {
        "task_related": task_bytes,
        "non_task_related": non_task_bytes,
        "total": task_bytes + non_task_bytes,
    }


def _refresh_memory_stats(memory_state):
    memory_state["approx_bytes"] = _memory_stats(memory_state)


def load_state(state_path):
    path = Path(state_path)
    if not path.exists():
        raise FileNotFoundError(f"State file does not exist: {path}")
    with path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    return ensure_state_schema(loaded)


def save_state(state_path, state):
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def _load_template_state(template_path):
    path = Path(template_path)
    if not path.exists():
        return deepcopy(DEFAULT_STATE)
    with path.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    return ensure_state_schema(loaded)


def init_state(state_path, template_path=None):
    path = Path(state_path)
    if path.exists():
        state = load_state(path)
        save_state(path, state)
        return {
            "ok": True,
            "created": False,
            "state_path": str(path),
            "pet": state["pet"],
        }
    state = _load_template_state(template_path or DEFAULT_TEMPLATE_STATE_PATH)
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


def _activity_pool(state):
    hunger = state["pet"]["hunger"]
    boredom = state["pet"]["boredom"]
    happiness = state["pet"]["happiness"]

    if boredom >= 55:
        return [
            "I chased a yarn ball around the room.",
            "I found a paper bag and turned it into a tunnel adventure.",
            "I practiced stealth steps and pounced on a toy mouse.",
        ]
    if hunger >= 45:
        return [
            "I sniffed around for snacks and took a small water break.",
            "I patrolled the kitchen and checked my water bowl.",
            "I followed food smells, then stretched near the bowl.",
        ]
    if happiness >= 70:
        return [
            "I made a new cat friend and we played tag.",
            "I took a sunny nap after a friendly head bump session.",
            "I watched birds by the window and chirped quietly.",
        ]
    return [
        "I groomed my fur and had a calm rest.",
        "I did a little paw exercise and drank some water.",
        "I explored a corner, then curled up for a short nap.",
    ]


def _pick_free_activity_hint(state, current_dt):
    last_hint = state["timing"].get("last_activity_hint_at")
    if last_hint is not None:
        minutes = _minutes_since(current_dt, last_hint)
        if minutes is not None and minutes < ACTIVITY_HINT_COOLDOWN_MINUTES:
            return None

    hunger = state["pet"]["hunger"]
    boredom = state["pet"]["boredom"]
    if hunger >= 75 or boredom >= 70:
        return None

    pool = _activity_pool(state)
    if not pool:
        return None

    chooser = int(current_dt.timestamp() // (TICK_MINUTES * 60))
    chooser += hunger + boredom + state["pet"]["happiness"]
    return pool[chooser % len(pool)]


def choose_action_candidate(state, current_dt):
    task_idx, task, task_reason = _pick_task_reminder(state, current_dt)
    if task is not None:
        state["tasks"][task_idx]["last_reminded_at"] = to_iso_utc(current_dt)
        return "task_reminder", f"task:{task_reason}", task

    action, reason = _pick_random_ping(state, current_dt)
    if action != "none":
        state["timing"]["last_random_ping_at"] = to_iso_utc(current_dt)
        return action, reason, None

    activity_hint = _pick_free_activity_hint(state, current_dt)
    if activity_hint is not None:
        state["timing"]["last_activity_hint_at"] = to_iso_utc(current_dt)
        return "cat_activity", "free_activity", {"activity_hint": activity_hint}

    return "none", "no_action_needed", None


def _compact_due_reason(memory, current_dt):
    if estimate_memory_bytes(memory) >= MEMORY_COMPACT_THRESHOLD_BYTES:
        return "size_limit"

    last_compact = parse_iso_utc(
        memory.get("last_compact_at"), "memory.last_compact_at"
    )
    local_now = current_dt.astimezone()
    if local_now.hour < MEMORY_COMPACT_HOUR_LOCAL:
        return None
    if last_compact is None or last_compact.astimezone().date() != local_now.date():
        return "daily_3am"
    return None


def _sync_due(last_sync_at, current_dt):
    last_sync = parse_iso_utc(last_sync_at, "memory.last_sync_at")
    if last_sync is None:
        return True
    return (current_dt - last_sync) >= timedelta(minutes=MEMORY_SYNC_INTERVAL_MINUTES)


def choose_memory_action_candidate(state, current_dt):
    memory = state["memory"]
    _refresh_memory_stats(memory)

    compact_reason = _compact_due_reason(memory, current_dt)
    if compact_reason:
        return {
            "type": "request_memory_compact",
            "reason": compact_reason,
            "max_bytes": MEMORY_COMPACT_THRESHOLD_BYTES,
            "task_related_max_bytes": TASK_MEMORY_LIMIT_BYTES,
            "non_task_related_max_bytes": NON_TASK_MEMORY_LIMIT_BYTES,
            "requested_at": to_iso_utc(current_dt),
            "notes": "Let LLM compact KickCat memory semantically.",
        }

    if _sync_due(memory.get("last_sync_at"), current_dt):
        return {
            "type": "request_memory_sync",
            "reason": "sync_interval",
            "interval_minutes": MEMORY_SYNC_INTERVAL_MINUTES,
            "task_related_max_bytes": TASK_MEMORY_LIMIT_BYTES,
            "non_task_related_max_bytes": NON_TASK_MEMORY_LIMIT_BYTES,
            "requested_at": to_iso_utc(current_dt),
            "notes": "Let LLM read main memory incrementally and return relevant summaries only.",
        }

    return {
        "type": "none",
        "reason": "not_due",
        "requested_at": to_iso_utc(current_dt),
    }


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

    candidate, reason, action_payload = choose_action_candidate(state, current_dt)

    memory_action = choose_memory_action_candidate(state, current_dt)

    state["meta"]["last_action_candidate"] = candidate
    state["meta"]["last_action_reason"] = reason
    state["timing"]["last_tick_at"] = to_iso_utc(current_dt)
    _refresh_memory_stats(state["memory"])
    save_state(state_path, state)

    out = {
        "ok": True,
        "ticks_applied": tick_count,
        "pet": state["pet"],
        "candidate": candidate,
        "reason": reason,
        "memory_action": memory_action,
    }
    if candidate == "task_reminder" and action_payload is not None:
        out["task_hint"] = action_payload.get("title")
    if candidate == "cat_activity" and action_payload is not None:
        out["activity_hint"] = action_payload.get("activity_hint")
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


def _normalize_memory_item(item, now_iso):
    if not isinstance(item, dict):
        raise ValueError("memory item must be an object")

    source_ref = item.get("source_ref")
    summary = item.get("summary")
    if not isinstance(source_ref, str) or not source_ref.strip():
        raise ValueError("memory item source_ref is required")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("memory item summary is required")

    topic = item.get("topic", "chat")
    if topic not in ("chat", "task", "mood", "pet"):
        topic = "chat"
    bucket = item.get("bucket")
    if bucket not in ("task_related", "non_task_related"):
        bucket = "task_related" if topic == "task" else "non_task_related"

    updated_at = item.get("updated_at")
    if updated_at is not None:
        parse_iso_utc(updated_at, "memory.updated_at")
    else:
        updated_at = now_iso

    synced_at = item.get("synced_at")
    if synced_at is not None:
        parse_iso_utc(synced_at, "memory.synced_at")
    else:
        synced_at = now_iso

    reason = item.get("reason")
    if reason is None:
        reason = "llm_semantic"
    elif not isinstance(reason, str):
        raise ValueError("memory item reason must be a string")

    normalized = {
        "id": item.get("id")
        or f"mem_{hashlib.sha1((source_ref.strip() + updated_at).encode('utf-8')).hexdigest()[:10]}",
        "source_ref": source_ref.strip(),
        "summary": summary.strip()[:180],
        "topic": topic,
        "updated_at": updated_at,
        "synced_at": synced_at,
        "reason": reason[:80],
        "bucket": bucket,
    }
    return normalized


def _trim_bucket(items, limit_bytes):
    ordered = sorted(items, key=lambda x: x.get("updated_at", ""), reverse=True)
    kept = []
    for item in ordered:
        kept.append(item)
        if _estimate_memory_bucket_bytes(kept) > limit_bytes:
            kept.pop()
            break
    return kept[:MEMORY_COMPACT_KEEP_ITEMS]


def _apply_memory_limits(memory):
    memory["task_related"] = _trim_bucket(
        memory.get("task_related", []), TASK_MEMORY_LIMIT_BYTES
    )
    memory["non_task_related"] = _trim_bucket(
        memory.get("non_task_related", []), NON_TASK_MEMORY_LIMIT_BYTES
    )
    _refresh_memory_stats(memory)


def _upsert_bucket(existing_items, new_items):
    existing_by_source = {
        item.get("source_ref"): item
        for item in existing_items
        if isinstance(item, dict) and item.get("source_ref")
    }
    for item in new_items:
        existing_by_source[item["source_ref"]] = item
    merged = sorted(
        existing_by_source.values(),
        key=lambda x: x.get("updated_at", ""),
        reverse=True,
    )
    return merged[:MEMORY_COMPACT_KEEP_ITEMS]


def _normalize_preference_key(key):
    allowed = {
        "reply_tone",
        "interaction_style",
        "quiet_hours",
        "reminder_density",
        "preferred_name",
    }
    return key if key in allowed else None


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

        elif op_type == "memory_sync_upsert":
            task_items = op.get("task_related_items", [])
            non_task_items = op.get("non_task_related_items", [])
            legacy_items = op.get("items")
            if legacy_items is not None:
                if not isinstance(legacy_items, list):
                    raise ValueError("memory_sync_upsert.items must be a list")
                for raw in legacy_items:
                    normalized_legacy = _normalize_memory_item(raw, now_iso)
                    if normalized_legacy["bucket"] == "task_related":
                        task_items.append(raw)
                    else:
                        non_task_items.append(raw)

            if not isinstance(task_items, list) or not isinstance(non_task_items, list):
                raise ValueError(
                    "memory_sync_upsert task_related_items/non_task_related_items must be lists"
                )

            normalized_task = []
            normalized_non_task = []
            for raw_item in task_items:
                normalized = _normalize_memory_item(raw_item, now_iso)
                normalized["bucket"] = "task_related"
                normalized_task.append(normalized)
            for raw_item in non_task_items:
                normalized = _normalize_memory_item(raw_item, now_iso)
                normalized["bucket"] = "non_task_related"
                normalized_non_task.append(normalized)

            state["memory"]["task_related"] = _upsert_bucket(
                state["memory"].get("task_related", []), normalized_task
            )
            state["memory"]["non_task_related"] = _upsert_bucket(
                state["memory"].get("non_task_related", []), normalized_non_task
            )
            state["memory"]["last_sync_at"] = now_iso

            cursor = op.get("cursor")
            if cursor is not None:
                if not isinstance(cursor, dict):
                    raise ValueError("memory_sync_upsert.cursor must be an object")
                updated_at = cursor.get("updated_at")
                cursor_id = cursor.get("id")
                if updated_at is not None:
                    parse_iso_utc(updated_at, "memory.cursor.updated_at")
                if cursor_id is not None and not isinstance(cursor_id, str):
                    raise ValueError("memory.cursor.id must be a string")
                state["memory"]["last_sync_cursor"] = {
                    "updated_at": updated_at,
                    "id": cursor_id,
                }
            applied_count += 1

        elif op_type == "memory_compact_replace":
            task_items = op.get("task_related_items")
            non_task_items = op.get("non_task_related_items")
            if not isinstance(task_items, list) or not isinstance(non_task_items, list):
                raise ValueError(
                    "memory_compact_replace requires task_related_items and non_task_related_items lists"
                )

            normalized_task = []
            normalized_non_task = []
            for item in task_items:
                normalized = _normalize_memory_item(item, now_iso)
                normalized["bucket"] = "task_related"
                normalized_task.append(normalized)
            for item in non_task_items:
                normalized = _normalize_memory_item(item, now_iso)
                normalized["bucket"] = "non_task_related"
                normalized_non_task.append(normalized)

            state["memory"]["task_related"] = normalized_task
            state["memory"]["non_task_related"] = normalized_non_task
            state["memory"]["last_compact_at"] = now_iso
            reason = op.get("reason", "llm_semantic_compact")
            if not isinstance(reason, str):
                raise ValueError("memory_compact_replace.reason must be a string")
            state["memory"]["last_compact_reason"] = reason[:80]
            applied_count += 1

        elif op_type == "preference_upsert":
            preferences = op.get("preferences")
            if not isinstance(preferences, dict):
                raise ValueError("preference_upsert.preferences must be an object")
            for raw_key, raw_value in preferences.items():
                if not isinstance(raw_key, str):
                    continue
                key = _normalize_preference_key(raw_key)
                if key is None:
                    continue
                if isinstance(raw_value, (str, int, float, bool)):
                    state["memory"]["user_preferences"][key] = str(raw_value)[:60]
            applied_count += 1

        else:
            raise ValueError(f"unsupported op type: {op_type}")

    clamp_pet(state)
    _apply_memory_limits(state["memory"])
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
    _refresh_memory_stats(state["memory"])
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
        "memory": {
            "task_related_items": len(state["memory"].get("task_related", [])),
            "non_task_related_items": len(state["memory"].get("non_task_related", [])),
            "approx_bytes": state["memory"].get(
                "approx_bytes", {"task_related": 0, "non_task_related": 0, "total": 0}
            ),
            "last_sync_at": state["memory"].get("last_sync_at"),
            "last_compact_at": state["memory"].get("last_compact_at"),
            "preferences": state["memory"].get("user_preferences", {}),
        },
    }


def _pet_level(value):
    if value >= 75:
        return "high"
    if value >= 40:
        return "medium"
    return "low"


def _public_summary(summary):
    pet = summary.get("pet", {})
    return {
        "pet_state": {
            "hunger": _pet_level(int(pet.get("hunger", 0))),
            "happiness": _pet_level(int(pet.get("happiness", 0))),
            "boredom": _pet_level(int(pet.get("boredom", 0))),
        },
        "candidate": summary.get("candidate", "none"),
        "task_hint": summary.get("task_hint"),
        "mood_hint": summary.get("mood_hint"),
        "memory_state": "stable"
        if summary.get("memory", {}).get("last_sync_at")
        else "idle",
    }


def _is_debug_cat_request(text):
    content = (text or "").strip()
    if content.lower().startswith("/cat"):
        content = content[4:].strip()
    return re.match(r"^debug(\s|$)", content, re.IGNORECASE) is not None


def _extract_task_title(text):
    lower = text.lower()
    markers = ["remind me to", "提醒我", "记得"]
    for marker in markers:
        idx = lower.find(marker)
        if idx >= 0:
            title = text[idx + len(marker) :].strip(" :，。,.!")
            if title:
                return title[:120]
    return None


def _extract_mood(text):
    lower = text.lower()
    mood_map = [
        ("stressed", -0.6, 0.7, ("stressed", "stress", "焦虑", "压力")),
        ("tired", -0.4, 0.6, ("tired", "exhausted", "累", "疲惫")),
        ("happy", 0.7, 0.6, ("happy", "great", "开心", "高兴")),
    ]
    for label, valence, intensity, words in mood_map:
        if any(word in lower for word in words):
            return {"label": label, "valence": valence, "intensity": intensity}
    return None


def build_cat_ops(text):
    ops: list[dict[str, Any]] = [{"type": "touch_interaction"}]
    intents = ["chat"]
    content = (text or "").strip()
    lower = content.lower()

    if any(word in lower for word in ("feed", "喂", "吃", "猫粮", "snack")):
        intents.append("feed")
        ops.append(
            {
                "type": "pet_delta",
                "field": "hunger",
                "feed_strength": 0.8,
                "reason": "cat_command_feed",
            }
        )
        ops.append(
            {
                "type": "pet_delta",
                "field": "happiness",
                "delta": 1,
                "reason": "cat_command_feed_bonus",
            }
        )

    ops.append(
        {
            "type": "pet_delta",
            "field": "happiness",
            "delta": 2,
            "reason": "cat_command_chat",
        }
    )
    ops.append(
        {
            "type": "pet_delta",
            "field": "boredom",
            "delta": -3,
            "reason": "cat_command_chat",
        }
    )

    task_title = _extract_task_title(content)
    if task_title:
        intents.append("task_capture")
        ops.append({"type": "task_add", "task": {"title": task_title}})

    mood = _extract_mood(content)
    if mood:
        intents.append("mood_capture")
        ops.append({"type": "mood_add", "mood": mood})

    return {"intents": sorted(set(intents)), "ops": ops}


def run_cat_command(state_path, text, now_dt=None):
    content = (text or "").strip()
    debug_mode = _is_debug_cat_request(content)
    if not content:
        summary = build_summary(state_path)
        return {
            "ok": True,
            "mode": "cat",
            "intents": ["chat"],
            "message": "KickCat is here. Tell me to chat, feed, remind, or share mood.",
            "summary": _public_summary(summary),
            "debug_mode": False,
        }

    mapping = build_cat_ops(content)
    payload = {
        "message_text": content,
        "ops": mapping["ops"],
    }
    apply_result = apply_ops(state_path, payload, now_dt=now_dt)
    summary = build_summary(state_path)
    out = {
        "ok": True,
        "mode": "cat",
        "intents": mapping["intents"],
        "apply": apply_result,
        "summary": _public_summary(summary),
        "debug_mode": debug_mode,
    }
    if debug_mode:
        out["debug_summary"] = summary
    return out


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
        return init_state(args.state_file, template_path=args.template_file)
    if args.command == "tick":
        return run_tick(args.state_file)
    if args.command == "apply":
        payload = _load_payload(args)
        return apply_ops(args.state_file, payload)
    if args.command == "cat":
        return run_cat_command(args.state_file, args.text)
    return build_summary(args.state_file)


def _add_runtime_flags(parser):
    parser.add_argument("--mode", choices=(DEFAULT_MODE, DEBUG_MODE), default=None)
    parser.add_argument("--debug-log-file", default=None)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="KickCat v1.2 local state script")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for cmd in ("init", "tick", "summary"):
        p = subparsers.add_parser(cmd)
        p.add_argument("--state-file", default=str(DEFAULT_STATE_PATH))
        if cmd == "init":
            p.add_argument("--template-file", default=str(DEFAULT_TEMPLATE_STATE_PATH))
        _add_runtime_flags(p)

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--state-file", default=str(DEFAULT_STATE_PATH))
    apply_parser.add_argument("--payload")
    apply_parser.add_argument("--payload-file")
    _add_runtime_flags(apply_parser)

    cat_parser = subparsers.add_parser("cat")
    cat_parser.add_argument("--state-file", default=str(DEFAULT_STATE_PATH))
    cat_parser.add_argument("--text", default="")
    _add_runtime_flags(cat_parser)
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
