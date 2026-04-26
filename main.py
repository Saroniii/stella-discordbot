# coding: UTF-8
import asyncio
import importlib
import discord
from discord.ext import commands
import os
import pathlib
import sys
from dotenv import load_dotenv
from utils.config_bind import bind_all_settings
from utils.storage import Storage
from utils.tick import TickMeter

load_dotenv()  

TOKEN = os.environ['TOKEN']
command_prefix = ['!']  # Prefix

class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        self.ready_check = False  # Variable to prevent duplicate on_ready events from being triggered
        self._config_bind_started = False
        self.config_bind_error: Exception | None = None
        self._config_bind_lock = asyncio.Lock()
        self.config_bind_ready = asyncio.Event()
        self.config_bind_ready.clear()
        self.system_reloading = False
        self.system_reloading_keep_active_cli = False
        self.tick_meter = TickMeter(Storage())
        super().__init__(*args, **kwargs)

    async def _safe_insert_system_log(
        self,
        storage: Storage,
        *,
        actor_user_id: int | None,
        scope_id: int,
        feature: str,
        severity: str,
        message: str,
        detail_json: dict,
    ) -> None:
        await storage.insert_system_log_safe(
            actor_user_id=actor_user_id,
            scope_id=scope_id,
            feature=feature,
            severity=severity,
            message=message,
            detail_json=detail_json,
        )

    async def setup_hook(self):
        if not self.ready_check:
            storage = Storage()
            await storage.init_schema()
            print('Preparing bot startup')
            print('import')
            folder_name = 'cogs'
            cur = pathlib.Path('.')

            for p in cur.glob(f"{folder_name}/*.py"):

                try:
                    print(f'cogs.{p.stem}', end="　")
                    await self.load_extension(f'cogs.{p.stem}')
                    print('success')
                    await self._safe_insert_system_log(
                        storage,
                        actor_user_id=None,
                        scope_id=0,
                        feature="cog-loader",
                        severity="info",
                        message="load-success",
                        detail_json={"phase": "startup", "extension": f"cogs.{p.stem}"},
                    )

                except commands.errors.NoEntryPointError:
                    print(f'module.{p.stem}')
                    await self._safe_insert_system_log(
                        storage,
                        actor_user_id=None,
                        scope_id=0,
                        feature="cog-loader",
                        severity="warn",
                        message="load-skipped-no-entrypoint",
                        detail_json={"phase": "startup", "extension": f"cogs.{p.stem}"},
                    )
                except Exception as exc:
                    await self._safe_insert_system_log(
                        storage,
                        actor_user_id=None,
                        scope_id=0,
                        feature="cog-loader",
                        severity="error",
                        message="load-failed",
                        detail_json={"phase": "startup", "extension": f"cogs.{p.stem}", "error_type": exc.__class__.__name__, "error": str(exc)},
                    )
                    raise

            self.ready_check = True

        else:
            print('The start up process is already complete!')

    async def ensure_config_bound(self):
        if self.config_bind_ready.is_set() and self.config_bind_error is None:
            return
        async with self._config_bind_lock:
            if self.config_bind_ready.is_set() and self.config_bind_error is None:
                return
            if self._config_bind_started:
                return
            self._config_bind_started = True
            self.config_bind_ready.clear()
            self.config_bind_error = None
            storage = Storage()
            try:
                await storage.init_schema()
                guild_ids = [int(guild.id) for guild in self.guilds if getattr(guild, "id", None) is not None]
                await bind_all_settings(storage, guild_ids, tick_meter=self.tick_meter)
            except Exception as exc:
                self.config_bind_error = exc
                self._config_bind_started = False
                self.config_bind_ready.set()
                await self._safe_insert_system_log(
                    storage,
                    actor_user_id=None,
                    scope_id=0,
                    feature="config-bind",
                    severity="error",
                    message="bind-runtime-failed",
                    detail_json={"error_type": exc.__class__.__name__, "error": str(exc)},
                )
                return
            self.config_bind_error = None
            self.config_bind_ready.set()

    async def rebind_all_configs(self) -> None:
        storage = Storage()
        await storage.init_schema()
        guild_ids = [int(guild.id) for guild in self.guilds if getattr(guild, "id", None) is not None]
        await bind_all_settings(storage, guild_ids, tick_meter=self.tick_meter)

    async def restart_runtime(self, keep_active_cli: bool = False) -> None:
        importlib.invalidate_caches()
        storage = Storage()
        await storage.init_schema()

        reloaded_utils = 0
        for module_name in sorted(name for name in sys.modules if name.startswith("utils.") and sys.modules.get(name) is not None):
            try:
                importlib.reload(sys.modules[module_name])
                reloaded_utils += 1
            except Exception:
                continue
        await self._safe_insert_system_log(
            storage,
            actor_user_id=None,
            scope_id=0,
            feature="cog-loader",
            severity="info",
            message="utils-reloaded",
            detail_json={"phase": "restart", "count": reloaded_utils},
        )

        extension_names = sorted(self.extensions.keys())
        for ext in extension_names:
            if keep_active_cli and ext == "cogs.cli":
                await self._safe_insert_system_log(
                    storage,
                    actor_user_id=None,
                    scope_id=0,
                    feature="cog-loader",
                    severity="info",
                    message="reload-skipped",
                    detail_json={"phase": "restart", "extension": ext, "reason": "keep-active-cli"},
                )
                continue
            try:
                await self.reload_extension(ext)
                await self._safe_insert_system_log(
                    storage,
                    actor_user_id=None,
                    scope_id=0,
                    feature="cog-loader",
                    severity="info",
                    message="reload-success",
                    detail_json={"phase": "restart", "extension": ext},
                )
            except commands.ExtensionNotLoaded:
                try:
                    await self.load_extension(ext)
                    await self._safe_insert_system_log(
                        storage,
                        actor_user_id=None,
                        scope_id=0,
                        feature="cog-loader",
                        severity="info",
                        message="load-success",
                        detail_json={"phase": "restart", "extension": ext},
                    )
                except Exception:
                    await self._safe_insert_system_log(
                        storage,
                        actor_user_id=None,
                        scope_id=0,
                        feature="cog-loader",
                        severity="error",
                        message="load-failed",
                        detail_json={"phase": "restart", "extension": ext},
                    )
                    continue
            except Exception:
                await self._safe_insert_system_log(
                    storage,
                    actor_user_id=None,
                    scope_id=0,
                    feature="cog-loader",
                    severity="error",
                    message="reload-failed",
                    detail_json={"phase": "restart", "extension": ext},
                )
                continue

        await self.rebind_all_configs()

    async def on_ready(self):
        if self.user is not None:
            print('Logged in as')
            print(self.user.name)
            print(self.user.id)
        await self.ensure_config_bound()


intent: discord.Intents = discord.Intents.all()
intent.message_content = True
bot = MyBot(command_prefix=command_prefix, intents=intent)

bot.run(TOKEN)
