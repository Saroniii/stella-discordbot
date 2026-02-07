# Discord CLI Spec v1

## Goal
Implement an NW-style CLI for Discord with strict, restorable configs.

## Architecture
- Discord adapter: `cogs/cli.py`
- CLI library: `utils/cli/*`
- Storage: `utils/storage.py`

## Core Commands
- `help`, `where`, `top`, `quit`
- `enter`, `leave`, `select`
- `set`, `unset`, `show`, `deploy`, `discard`
- `get counters all`
- `get log audit [limit]`, `get log system [limit]`
- `get log crash [limit]`
- `diagnose database`
- `switch root`, `switch guild <guild-id>` (bot admin only)

## Sections
- `welcome`
- `log-config`
- `log-type`
- `guild-log/mod-log`
- `guild-log/message-log`
- `guild-log/member-log`
- `root-defaults`
- `root-enforce`
- `control-plane/root-connection`
- `tenant-connection/log`

## Validation
- Pydantic strict mode (`strict=True`, `extra='forbid'`)
- Validation occurs on `set`/`unset`
- Error format: `field=<...> reason=<...> hint=<...>`

## Persistence
- `DATABASE_URL` -> PostgreSQL
- fallback -> SQLite (`data/stella.db`)
- Tables:
  - `configs`
  - `audit_logs`
  - `system_logs`
- Config payload format:
  - `schema_version`
  - `payload`

## Root Policies
- `root-defaults`: auto-seed on first access
- `root-enforce`: always highest priority
- guild-level `set` for enforced keys is rejected

## Logging
- `get log audit/system` default limit: 50
- `get log crash` default limit: 50
- Ring buffer policy: drop oldest entries on overflow
- `log-config` buffer range: `100..100000`
- `log-type` severity: `debug|info|warn|error`
- unspecified feature severity defaults to `info`
- `tenant-connection/log` receive mode: `off|discord|database|both`

## Session Model
- One active session per guild
- Session owner-only input
- Timeout: 10 minutes
- Session channel: thread created by `!cli`
