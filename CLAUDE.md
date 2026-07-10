@AGENTS.md

## Claude Code

- Cloud sessions (claude.ai/code, `CLAUDE_CODE_REMOTE=true`): install missing dev tools with
  `bash scripts/setup-dev-tools.sh`; start the database with `make db` (Docker Compose).
- Handbook plugin (`handbook@nicograef`, marketplace repo `nicograef/handbook`): loads in cloud
  sessions only if `nicograef/handbook` is in the session's GitHub scope at session start —
  otherwise the git proxy blocks the marketplace clone and the `/handbook:*` skills are missing.
  Workaround in a running session (hereby authorized): add `nicograef/handbook` via `add_repo`,
  clone it, call `register_repo_root`, and follow the skills from `<clone>/.claude/skills/` directly.

## Non-negotiables (survive compaction)

- Do not commit without explicit user approval — propose the message first.
- Never `git push --force` / `-f` / `--force-with-lease` or `--no-verify`.
