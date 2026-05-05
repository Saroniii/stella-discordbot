# Stella Discord Bot

Stella is a Discord bot with server management features, thread-based admin CLI settings, guild logs, chat groups, sticky messages, and a level/ranking system.

## Setup

1. Install dependencies.

   ```bash
   pip install -r requirements.txt
   ```

2. Set the bot token.

   ```bash
   export TOKEN="your-discord-bot-token"
   ```

3. Start the bot.

   ```bash
   python main.py
   ```

By default, the bot uses SQLite at `data/stella.db`. Set `DATABASE_URL` to use PostgreSQL.

## Basic Commands

- `!cli` opens the admin CLI. The user must have Discord's Manage Guild permission.
- `!rank` shows your current level, XP, rank, and progress to the next level.
- `!ranking [limit]` shows the server level ranking. The limit is clamped to `1..50`.

## Admin CLI Flow

Run `!cli` in a guild channel. Stella creates a CLI session in a thread unless the console config is changed.

Useful first commands:

```text
help
?
enter ?
show
```

Typical config edit flow:

```text
enter welcome
show
set welcome-message "Welcome {mention}!"
deploy
quit
```

The CLI keeps a running config during the session. Use `deploy` to persist it as startup config, or `discard` to restore the session state from startup config.

## Checks

Run the full project checks before handing off changes.

```bash
pytest
python -m mypy --explicit-package-bases .
python -m ruff check .
```
