"""User management endpoints."""

import uuid
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.auth.deps import require_user
from app.database import get_session
from app.models import User
from app.schemas import UserCreate, UserResponse, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: Annotated[User, Depends(require_user)]):
    return current_user


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    body: UserCreate,
    _current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(User).where(User.email == body.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(
        id=uuid.uuid4(),
        email=body.email,
        full_name=body.full_name,
        password_hash=_hash(body.password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@router.get("", response_model=list[UserResponse])
async def list_users(
    _current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(User).order_by(User.created_at))
    return result.scalars().all()


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    _current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    if _current_user is not None and _current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot modify another user")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if body.full_name is not None:
        user.full_name = body.full_name
    if body.email is not None:
        existing = await session.execute(
            select(User).where(User.email == body.email, User.id != user_id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        user.email = body.email
    if body.password is not None:
        user.password_hash = _hash(body.password)

    await session.commit()
    await session.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def deactivate_user(
    user_id: uuid.UUID,
    _current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    if _current_user is not None and _current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot deactivate another user")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    user.is_active = False
    await session.commit()
