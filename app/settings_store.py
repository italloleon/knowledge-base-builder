"""DB-backed settings store with environment variable fallback.

Settings are stored in ``curadoria.system_settings``.  On read, the DB
value takes precedence; if absent or empty the env var equivalent is used.
Secrets are never returned in plain-text through the public API — callers
get a boolean ``is_configured`` flag instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings as env_settings


@dataclass(frozen=True)
class SettingDefinition:
    key: str
    label: str
    is_secret: bool = True
    default: str = ""
    # Name of the corresponding env var (defaults to key.upper())
    env_var: str = ""

    def resolved_env_var(self) -> str:
        return self.env_var or self.key.upper()


# All known settings — order determines UI display order.
SETTING_DEFINITIONS: list[SettingDefinition] = [
    SettingDefinition(
        key="enrichment_provider",
        label="Enrichment Provider",
        is_secret=False,
        default="gemini",
        env_var="ENRICHMENT_PROVIDER",
    ),
    SettingDefinition(
        key="gemini_api_key",
        label="Gemini API Key",
        is_secret=True,
        env_var="GEMINI_API_KEY",
    ),
    SettingDefinition(
        key="gemini_model",
        label="Gemini Model",
        is_secret=False,
        default="gemini-2.5-flash",
        env_var="GEMINI_MODEL",
    ),
    SettingDefinition(
        key="openai_api_key",
        label="OpenAI API Key",
        is_secret=True,
        env_var="OPENAI_API_KEY",
    ),
    SettingDefinition(
        key="openai_model",
        label="OpenAI Model",
        is_secret=False,
        default="gpt-4o-mini",
        env_var="OPENAI_MODEL",
    ),
    SettingDefinition(
        key="deepseek_api_key",
        label="DeepSeek API Key",
        is_secret=True,
        env_var="DEEPSEEK_API_KEY",
    ),
    SettingDefinition(
        key="deepseek_model",
        label="DeepSeek Model",
        is_secret=False,
        default="deepseek-chat",
        env_var="DEEPSEEK_MODEL",
    ),
    SettingDefinition(
        key="anthropic_api_key",
        label="Anthropic API Key",
        is_secret=True,
        env_var="ANTHROPIC_API_KEY",
    ),
    SettingDefinition(
        key="anthropic_model",
        label="Anthropic Model",
        is_secret=False,
        default="claude-haiku-4-5-20251001",
        env_var="ANTHROPIC_MODEL",
    ),
]

_DEFINITIONS_BY_KEY: dict[str, SettingDefinition] = {
    d.key: d for d in SETTING_DEFINITIONS
}


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


async def get_setting(session: AsyncSession, key: str) -> str | None:
    """Return the effective value for *key* (DB first, env var fallback)."""
    from app.models import SystemSetting  # local import avoids circular deps

    row = await session.get(SystemSetting, key)
    if row and row.value:
        return row.value

    defn = _DEFINITIONS_BY_KEY.get(key)
    if defn:
        env_value = getattr(env_settings, defn.resolved_env_var(), None)
        if env_value:
            return str(env_value)
        if defn.default:
            return defn.default

    return None


async def get_all_settings(session: AsyncSession) -> list[dict[str, Any]]:
    """Return all known settings for the API response.

    Secrets have their value replaced with a boolean ``is_configured`` flag
    so API keys are never leaked through the settings endpoint.
    """
    from app.models import SystemSetting

    rows_result = await session.execute(select(SystemSetting))
    db_rows: dict[str, str] = {
        row.key: row.value
        for row in rows_result.scalars()
        if row.value
    }

    result = []
    for defn in SETTING_DEFINITIONS:
        db_val = db_rows.get(defn.key)
        env_val = getattr(env_settings, defn.resolved_env_var(), None) or defn.default

        if defn.is_secret:
            is_configured = bool(db_val or env_val)
            source = "db" if db_val else ("env" if env_val else "none")
            result.append({
                "key": defn.key,
                "label": defn.label,
                "is_secret": True,
                "is_configured": is_configured,
                "source": source,
            })
        else:
            value = db_val or env_val or ""
            result.append({
                "key": defn.key,
                "label": defn.label,
                "is_secret": False,
                "value": value,
                "source": "db" if db_val else ("env" if env_val else "default"),
            })

    return result


# ---------------------------------------------------------------------------
# Write helper
# ---------------------------------------------------------------------------


async def upsert_setting(session: AsyncSession, key: str, value: str) -> None:
    """Create or update a setting.  Raises ValueError for unknown keys."""
    from app.models import SystemSetting

    if key not in _DEFINITIONS_BY_KEY:
        raise ValueError(f"Unknown setting key: {key!r}")

    defn = _DEFINITIONS_BY_KEY[key]
    row = await session.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, is_secret=defn.is_secret)
        session.add(row)
    row.value = value or None  # store None for empty strings (= "not configured")
    await session.commit()
