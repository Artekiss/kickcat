# AGENT.md

## Scope
This file applies to the current workspace and all subdirectories unless a deeper AGENT.md overrides it.

## Environment Baseline
- OS: Arch Linux on WSL2
- Default home directory: /root
- Default user: root
- System locale default: en_US.UTF-8
- Generated locales available: en_US.utf8, zh_CN.utf8
- Chinese display support available via fontconfig + noto-fonts-cjk
- Git repository may not exist in every working folder

## Collaboration Defaults
- Prefer concise, actionable responses.
- Explain key tradeoffs briefly when proposing changes.
- Ask only one focused question when blocked by ambiguity.
- Do not perform destructive operations unless explicitly requested.

## File and Code Rules
- Prefer minimal, targeted edits over broad rewrites.
- Keep content ASCII by default unless Unicode is already in use.
- Add comments only when a block is non-obvious.
- Follow existing project conventions first (naming, structure, style).

## Tooling Preferences
- Prefer dedicated read/search/edit tools for file operations.
- Use terminal commands only when needed and report important output succinctly.
- Run independent checks in parallel when safe.

## Toolchain Baseline
- Python runtime available (major: 3.x).
- `uv` available as default Python workflow tool (major: 0.x).
- Git available for version control (major: 2.x).
- OpenSSH available for SSH operations (major: 10.x).
- Build essentials available via `base-devel`.

## Validation
- After code changes, run the smallest relevant validation first.
- If full test/build is expensive, state what was run and what was not run.

## Git Hygiene
- Never revert unrelated user changes.
- Never amend commits unless explicitly requested.
- Never force-push without explicit user approval.

## Security and Access Control
- Do not print or commit secrets (tokens, credentials, private keys).
- Treat auth and config files as sensitive by default.
- `/root` is the default authoritative workspace.
- Strictly prohibit autonomous access to `/mnt` paths.
- Access to any `/mnt` path is allowed only when the user explicitly requests it and provides per-request approval each time.
- Keep any approved `/mnt` operation limited to the requested path and action scope.
