import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.jwt import decode_access_token
from app.config import settings
from app.database import get_session
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: AsyncSession = Depends(get_session),
) -> User | None:
    if not token:
        if not settings.AUTH_ENABLED:
            return None  # dev bypass — no token provided
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    # Token present — always resolve it regardless of AUTH_ENABLED
    payload = decode_access_token(token)
    user_id = uuid.UUID(payload["sub"])

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    return user


async def require_user(
    user: Annotated[User | None, Depends(get_current_user)],
) -> User | None:
    """Returns the resolved User, or None when AUTH_ENABLED=false and no token was sent."""
    if user is None and settings.AUTH_ENABLED:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def require_account(
    user: Annotated[User | None, Depends(get_current_user)],
) -> User:
    """Always requires a logged-in user (study notes, timers, etc.)."""
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user
