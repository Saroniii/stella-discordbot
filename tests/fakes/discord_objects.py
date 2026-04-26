from __future__ import annotations

from typing import Any


class FakeUser:
    def __init__(self, user_id: int = 1, *, name: str = "user", bot: bool = False) -> None:
        self.id = int(user_id)
        self.name = name
        self.bot = bot
        self.mention = f"<@{self.id}>"

    def __str__(self) -> str:
        return self.name


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = int(role_id)


class FakeMember(FakeUser):
    def __init__(
        self,
        user_id: int = 1,
        *,
        name: str = "member",
        bot: bool = False,
        roles: list[FakeRole] | None = None,
        nick: str | None = None,
    ) -> None:
        super().__init__(user_id, name=name, bot=bot)
        self.roles = roles or []
        self.nick = nick
        self.guild = None


class FakeChannel:
    def __init__(self, channel_id: int = 1, *, nsfw: bool = False) -> None:
        self.id = int(channel_id)
        self.nsfw = nsfw
        self.sent: list[dict[str, Any]] = []
        self.deleted = False

    async def send(self, content: str | None = None, **kwargs: Any) -> Any:
        payload = {"content": content, **kwargs}
        self.sent.append(payload)
        return FakeMessage(channel=self, content=content or "")

    async def delete(self) -> None:
        self.deleted = True

    def is_nsfw(self) -> bool:
        return bool(self.nsfw)


class FakeMessage:
    def __init__(
        self,
        *,
        message_id: int = 1,
        channel: FakeChannel | None = None,
        author: FakeUser | None = None,
        content: str = "",
    ) -> None:
        self.id = int(message_id)
        self.channel = channel or FakeChannel()
        self.author = author or FakeUser()
        self.content = content
        self.reactions: list[Any] = []
        self.deleted = False
        self.guild = None

    async def add_reaction(self, emoji: Any) -> None:
        self.reactions.append(emoji)

    async def delete(self) -> None:
        self.deleted = True


class FakeGuild:
    def __init__(self, guild_id: int = 1, *, channels: list[FakeChannel] | None = None) -> None:
        self.id = int(guild_id)
        self._channels = {channel.id: channel for channel in channels or []}
        self.fetch_calls: list[int] = []

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self._channels.get(int(channel_id))

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        self.fetch_calls.append(int(channel_id))
        channel = self._channels[int(channel_id)]
        return channel


class FakeBot:
    def __init__(self, *, guilds: list[FakeGuild] | None = None, channels: list[FakeChannel] | None = None) -> None:
        self._guilds = {guild.id: guild for guild in guilds or []}
        self._channels = {channel.id: channel for channel in channels or []}
        self.fetch_channel_calls: list[int] = []

    def get_guild(self, guild_id: int) -> FakeGuild | None:
        return self._guilds.get(int(guild_id))

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self._channels.get(int(channel_id))

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        self.fetch_channel_calls.append(int(channel_id))
        channel = self._channels[int(channel_id)]
        return channel


class FakeWebhook:
    def __init__(self, webhook_id: int = 1, token: str | None = "token") -> None:
        self.id = int(webhook_id)
        self.token = token
        self.sent: list[dict[str, Any]] = []
        self.deleted = False

    async def send(self, **kwargs: Any) -> FakeMessage:
        self.sent.append(kwargs)
        return FakeMessage(content=str(kwargs.get("content") or ""))

    async def delete(self, **kwargs: Any) -> None:
        self.deleted = True
