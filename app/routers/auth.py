"""Auth endpoints — login, refresh, logout."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
import bcrypt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.deps import require_user
from app.auth.jwt import (
    create_access_token,
    create_refresh_token,
    hash_token,
    refresh_token_expires_at,
)
from app.database import get_session
from app.models import RefreshToken, User
from app.schemas import LoginRequest, RefreshRequest, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _verify(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active or not _verify(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    access_token = create_access_token(user.id, user.email)
    raw_refresh, token_hash = create_refresh_token()

    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=refresh_token_expires_at(),
        )
    )
    await session.commit()

    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)):
    token_hash = hash_token(body.refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()

    if stored is None or stored.revoked or stored.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    user_result = await session.execute(select(User).where(User.id == stored.user_id))
    user = user_result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Rotate: revoke old token, issue new one atomically
    stored.revoked = True
    raw_refresh, new_token_hash = create_refresh_token()
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=new_token_hash,
            expires_at=refresh_token_expires_at(),
        )
    )
    await session.commit()

    return TokenResponse(
        access_token=create_access_token(user.id, user.email),
        refresh_token=raw_refresh,
    )


@router.post("/logout", status_code=204)
async def logout(
    body: RefreshRequest,
    _current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    token_hash = hash_token(body.refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    if stored:
        if stored.user_id != _current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token does not belong to caller")
        stored.revoked = True
        await session.commit()
