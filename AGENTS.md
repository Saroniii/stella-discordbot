# AGENTS.md

## Development Principles

- Preserve public behavior unless a task explicitly asks to change it. CLI commands, CLI output strings, database schema, and existing stored config compatibility are treated as public behavior.
- Prefer shared helpers over local one-off implementations. Before adding logic to a Cog, CLI section, service, or storage method, check whether the same operation already exists in `utils/`.
- Keep refactors incremental. Storage and Discord side effects are sensitive areas; introduce small helpers first, then migrate call sites with tests.

## Shared Utility Boundaries

- Runtime config loading belongs in `utils/config_runtime.py`.
  - Use `extract_running_payload()` for envelope/plain payload handling.
  - Use `load_running_section()` or `load_guild_running_section()` instead of repeating `load_config()` plus payload extraction.
  - Use `ensure_bind_ready()` for Cog startup/config bind waiting.

- Discord-adjacent helpers belong in `utils/discord_helpers.py`.
  - Use `resolve_guild_channel()` and `resolve_bot_channel()` for cache lookup plus fetch fallback.
  - Use `safe_int()`, `trim_text()`, `discord_timestamp_now()`, `discord_timestamp_from_datetime()`, and `parse_discord_color()` instead of duplicating local variants.
  - Keep business decisions in the Cog or service. Shared Discord helpers should do narrow mechanical work only.

- CLI formatting helpers belong in `utils/cli/formatter.py`.
  - Use shared `CliNode` helpers such as `no_settings_node()`, `empty_select_node()`, and config-pair render helpers for repeated `now-config` / `deploy-config` output.
  - Do not change existing rendered text unless the task explicitly updates the CLI contract.

- CLI section behavior belongs in `utils/cli/sections/base.py` or a focused shared section module.
  - Use `PydanticSectionSpec` / `PydanticMappedSectionSpec` for standard Pydantic default/validation sections.
  - Use shared ID and error helpers for common `select <id>`, duplicate ID, and row lookup behavior.
  - Put reusable section families in their own module, as with `utils/cli/sections/level_table.py`.

## Storage Guidelines

- Do not introduce a broad ORM or rewrite SQL wholesale.
- When PG and SQLite branches differ only in row shape, prefer mapper helpers over duplicated dataclass construction.
- Keep tenant table naming and schema creation explicit unless a small metadata helper can reduce duplication without hiding backend differences.
- Safe insert/fetch wrappers should preserve current failure behavior and logging expectations.

## Cog Guidelines

- Cogs should orchestrate Discord events and feature-specific policy, not duplicate config extraction, bind waiting, channel resolution, or formatting helpers.
- Utility webhook creation/sending/deletion should be shared where practical, while Cog-specific permission checks and status messages stay local.
- If a helper would need many feature-specific flags, keep that part in the Cog and extract only the mechanical repeated operation.

## Test Guidelines

- Shared Discord fakes live in `tests/fakes/discord_objects.py`. Do not create `tests/fakes/discord.py`, because that name can be confused with the `discord` library.
- Add focused helper tests when adding shared utilities.
- After refactors, run `pytest`. For narrower checks, include:
  - CLI: `tests/test_engine.py`, `tests/test_formatter.py`, `tests/test_level_gain_policy_section.py`
  - Storage: `tests/test_storage.py`, `tests/test_level_service.py`
  - Cogs: `tests/test_chat_group_cog.py`, `tests/test_sticky_auto_cog.py`, `tests/test_guild_log_cog.py`, `tests/test_cli_cog_integration.py`
