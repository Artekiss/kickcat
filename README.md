# KickCat v1.2

KickCat is a minimal virtual pet reminder skill for OpenClaw.

It keeps one tight loop:

- maintain pet state (`hunger`, `happiness`, `boredom`)
- update state on heartbeat tick
- apply all state changes through one reducer (`apply`)
- request memory sync/compact actions for LLM, without mutating main agent memory

Version semantics:

- product/release version: `v1.2`
- state schema version in runtime state: `version = 2` (schema migration marker)

## Project layout

- `skills/kickcat/kickcat.py`: local script (`init`, `tick`, `apply`, `summary`, `cat`)
- `skills/kickcat/SKILL.md`: skill contract and routing guidance
- `data/kickcat.json`: tracked template state
- `data/kickcat-running.json`: runtime state (gitignored)
- `HEARTBEAT.md`: tiny heartbeat checklist
- `tests/`: unit and CLI tests

## Core commands

```bash
python3 skills/kickcat/kickcat.py init
python3 skills/kickcat/kickcat.py tick
python3 skills/kickcat/kickcat.py summary
python3 skills/kickcat/kickcat.py cat --text "feed cat and remind me to write report"
python3 skills/kickcat/kickcat.py apply --payload '{"ops":[{"type":"touch_interaction"}]}'
```

`init` behavior:

- if `data/kickcat-running.json` exists, keep and normalize it
- if missing, create it from `data/kickcat.json` template
- override paths with `--state-file` and `--template-file`

## Deploy and debug modes

- `--mode deploy` (default): stable minimal error payload
- `--mode debug`: crash-safe JSON output + debug log

```bash
python3 skills/kickcat/kickcat.py summary --mode debug --debug-log-file logs/kickcat-debug.jsonl
```

Environment overrides:

- `KICKCAT_MODE`
- `KICKCAT_DEBUG_LOG_FILE`

## LLM-driven memory workflow

KickCat does **not** read or modify OpenClaw main memory directly.

- `tick` emits `memory_action`:
  - `request_memory_sync` (every 3 hours)
  - `request_memory_compact` (daily after 03:00 local or when memory total reaches 32KB)
- OpenClaw/LLM performs semantic sync/compact externally, then writes back via `apply`.

Reducer ops for this flow:

- `memory_sync_upsert` with:
  - `task_related_items`
  - `non_task_related_items`
  - optional `cursor` (`updated_at + id`)
- `memory_compact_replace` with:
  - `task_related_items`
  - `non_task_related_items`

Memory limits (UTF-8 bytes):

- task-related bucket: `<= 8KB`
- non-task bucket: `<= 4KB`
- total compact trigger threshold: `>= 32KB`

## Reply and activity policy

- strict debug gating: only explicit `/cat DEBUG ...` may include numeric status/debug details
- default `/cat` replies stay non-numeric and user-facing
- `tick` may emit `candidate=cat_activity` with `activity_hint` when no task/urgent reminder is due

## Lightweight learning

KickCat supports small preference evolution through `preference_upsert`.

- whitelist fields only (`reply_tone`, `interaction_style`, `quiet_hours`, `reminder_density`, `preferred_name`)
- no heavy profile system
- keeps behavior adaptive without over-engineering

## Persistence guarantee

- Skills code updates must not clear KickCat local memory/state.
- `init` is idempotent and only fills missing schema fields.

## Testing

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT
