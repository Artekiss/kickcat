# KickCat v1

KickCat is a minimal virtual pet reminder skill for OpenClaw.

It focuses on one closed loop only:

- keep pet state (`hunger`, `happiness`, `boredom`)
- advance state on heartbeat ticks
- apply chat/feed/task/mood updates through a single reducer
- decide whether to remind, ping, or stay silent (`HEARTBEAT_OK`)

## Project layout

- `skills/kickcat/kickcat.py`: local state script (`init`, `tick`, `apply`, `summary`)
- `skills/kickcat/SKILL.md`: skill contract and reducer mapping
- `data/kickcat.json`: single source of truth for state
- `HEARTBEAT.md`: tiny heartbeat workflow checklist
- `tests/`: unit and CLI integration tests

## State model

`data/kickcat.json` stores:

- `version`
- `pet` (`name`, `hunger`, `happiness`, `boredom`)
- `timing` (`last_tick_at`, `last_feed_at`, `last_interaction_at`, `last_random_ping_at`)
- `tasks` (light reminder objects)
- `moods` (minimal mood entries)
- `meta` (`last_action_candidate`, `last_action_reason`, `last_message_hash`)

Rules:

- pet numeric fields are always clamped to `0..100`
- timestamps use ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`)
- all mutations go through reducer ops in `apply`

## CLI usage

Initialize state (idempotent):

```bash
python3 skills/kickcat/kickcat.py init
```

Apply reducer ops:

```bash
python3 skills/kickcat/kickcat.py apply --payload '{"ops":[{"type":"touch_interaction"}]}'
```

Heartbeat tick:

```bash
python3 skills/kickcat/kickcat.py tick
```

Read compact summary:

```bash
python3 skills/kickcat/kickcat.py summary
```

## Deploy and debug modes

The script supports runtime mode controls:

- `--mode deploy` (default): stable minimal error payload, non-zero exit on failure
- `--mode debug`: crash-safe JSON output with fallback and debug log writing

Optional debug log path:

```bash
python3 skills/kickcat/kickcat.py summary --mode debug --debug-log-file logs/kickcat-debug.jsonl
```

Environment overrides:

- `KICKCAT_MODE`
- `KICKCAT_DEBUG_LOG_FILE`

## OpenClaw heartbeat recommendation

Start internal testing with:

- `every: "10m"`
- `target: "none"`

After stable validation, switch to:

- `target: "last"`

Heartbeat flow remains: `tick -> summary -> HEARTBEAT_OK or one short message`.

## Testing

Run all tests:

```bash
python3 -m unittest discover -s tests -v
```

Current suite covers:

- command behavior (`init`, `tick`, `apply`, `summary`)
- reducer validation and safeguards
- feed cooldown and duplicate-message protection
- task/mood auto-fill and validation
- deploy/debug mode error handling and debug log fallback

## v1 scope boundaries

This v1 intentionally does not include:

- database or external services
- vector memory
- workflow orchestrators or web APIs
- complex task lifecycle systems

It is a small, local, testable loop by design.

## License

MIT
