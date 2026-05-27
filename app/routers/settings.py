"""Admin settings endpoints — API key management and provider configuration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.providers.registry import list_providers
from app.settings_store import SETTING_DEFINITIONS, get_all_settings, upsert_setting

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SettingPatch(BaseModel):
    key: str
    value: str


class SettingsBatchPatch(BaseModel):
    settings: list[SettingPatch]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_session)):
    """Return all known settings.

    Secret values (API keys) are masked — the response indicates whether a
    key is configured, but never returns the key itself.
    """
    return {
        "settings": await get_all_settings(session),
        "providers": list_providers(),
    }


@router.patch("/settings")
async def update_settings(
    body: SettingsBatchPatch,
    session: AsyncSession = Depends(get_session),
):
    """Create or update one or more settings.

    Sending an empty string for a key clears it (falls back to env var).
    """
    known_keys = {d.key for d in SETTING_DEFINITIONS}
    errors: list[str] = []

    for patch in body.settings:
        if patch.key not in known_keys:
            errors.append(f"Unknown setting key: {patch.key!r}")
            continue
        try:
            await upsert_setting(session, patch.key, patch.value)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{patch.key}: {exc}")

    if errors:
        raise HTTPException(status_code=422, detail=errors)

    return {"settings": await get_all_settings(session)}
