from __future__ import annotations

from types import SimpleNamespace

from utils.timezone_resolver import resolve_display_timezone_with_meta


def test_timezone_resolver_prefers_explicit_config():
    guild = SimpleNamespace(voice_channels=[SimpleNamespace(rtc_region="japan")], preferred_locale="en-US")
    resolved = resolve_display_timezone_with_meta(guild, "Europe/London")
    assert resolved.timezone == "Europe/London"
    assert resolved.source == "config"


def test_timezone_resolver_uses_rtc_region():
    guild = SimpleNamespace(voice_channels=[SimpleNamespace(rtc_region="japan")], preferred_locale=None)
    resolved = resolve_display_timezone_with_meta(guild, None)
    assert resolved.timezone == "Asia/Tokyo"
    assert resolved.source == "rtc_region"


def test_timezone_resolver_uses_locale_fallback():
    guild = SimpleNamespace(voice_channels=[SimpleNamespace(rtc_region=None)], preferred_locale="ja")
    resolved = resolve_display_timezone_with_meta(guild, None)
    assert resolved.timezone == "Asia/Tokyo"
    assert resolved.source == "preferred_locale"


def test_timezone_resolver_falls_back_to_utc():
    guild = SimpleNamespace(voice_channels=[SimpleNamespace(rtc_region="unknown-region")], preferred_locale="zz")
    resolved = resolve_display_timezone_with_meta(guild, None)
    assert resolved.timezone == "UTC"
    assert resolved.source == "fallback"
    assert resolved.unresolved_region == "unknown-region"
