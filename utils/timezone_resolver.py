from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RTC_REGION_TO_TZ: dict[str, str] = {
    "japan": "Asia/Tokyo",
    "singapore": "Asia/Singapore",
    "us-west": "America/Los_Angeles",
    "us-east": "America/New_York",
    "europe": "Europe/London",
}

LOCALE_TO_TZ: dict[str, str] = {
    "ja": "Asia/Tokyo",
    "en-us": "America/New_York",
    "en-gb": "Europe/London",
    "ko": "Asia/Seoul",
    "zh-cn": "Asia/Shanghai",
    "zh-tw": "Asia/Taipei",
}


@dataclass(frozen=True)
class TimezoneResolution:
    timezone: str
    source: str
    unresolved_region: str | None = None


def resolve_display_timezone(guild: Any | None, configured_timezone: str | None) -> str:
    return resolve_display_timezone_with_meta(guild, configured_timezone).timezone


def resolve_display_timezone_with_meta(guild: Any | None, configured_timezone: str | None) -> TimezoneResolution:
    if configured_timezone:
        return TimezoneResolution(timezone=configured_timezone, source="config")

    unresolved_region: str | None = None
    voice_channels = list(getattr(guild, "voice_channels", []) or [])
    for channel in voice_channels:
        region_name = _extract_rtc_region_name(getattr(channel, "rtc_region", None))
        if not region_name:
            continue
        timezone_name = RTC_REGION_TO_TZ.get(region_name)
        if timezone_name:
            return TimezoneResolution(timezone=timezone_name, source="rtc_region")
        unresolved_region = region_name

    locale = str(getattr(guild, "preferred_locale", "") or "").strip().lower()
    if locale:
        if locale in LOCALE_TO_TZ:
            return TimezoneResolution(timezone=LOCALE_TO_TZ[locale], source="preferred_locale")
        language_code = locale.split("-")[0]
        if language_code in LOCALE_TO_TZ:
            return TimezoneResolution(timezone=LOCALE_TO_TZ[language_code], source="preferred_locale")

    return TimezoneResolution(timezone="UTC", source="fallback", unresolved_region=unresolved_region)


def _extract_rtc_region_name(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        token = str(getattr(value, "value") or "")
    elif hasattr(value, "name"):
        token = str(getattr(value, "name") or "")
    else:
        token = str(value)
    token = token.strip().lower().replace("_", "-")
    if not token:
        return None
    return token
