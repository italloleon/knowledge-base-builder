"""Question opinion endpoints."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.auth.deps import require_user
from app.database import get_session
from app.models import Question, QuestionOpinion, User
from app.schemas import OpinionCreate, OpinionResponse, OpinionUpdate

router = APIRouter(tags=["opinions"])


@router.post("/questions/{question_id}/opinions", response_model=OpinionResponse, status_code=201)
async def create_opinion(
    question_id: uuid.UUID,
    body: OpinionCreate,
    current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    q_result = await session.execute(select(Question).where(Question.id == question_id))
    if q_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    opinion = QuestionOpinion(
        id=uuid.uuid4(),
        question_id=question_id,
        user_id=current_user.id,
        target=body.target,
        body=body.body,
    )
    session.add(opinion)
    await session.commit()

    result = await session.execute(
        select(QuestionOpinion)
        .where(QuestionOpinion.id == opinion.id)
        .options(selectinload(QuestionOpinion.user))
    )
    opinion = result.scalar_one()
    return _to_response(opinion)


@router.get("/questions/{question_id}/opinions", response_model=list[OpinionResponse])
async def list_opinions(
    question_id: uuid.UUID,
    _current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(QuestionOpinion)
        .where(QuestionOpinion.question_id == question_id)
        .options(selectinload(QuestionOpinion.user))
        .order_by(QuestionOpinion.created_at)
    )
    return [_to_response(o) for o in result.scalars().all()]


@router.patch("/opinions/{opinion_id}", response_model=OpinionResponse)
async def update_opinion(
    opinion_id: uuid.UUID,
    body: OpinionUpdate,
    current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(QuestionOpinion)
        .where(QuestionOpinion.id == opinion_id)
        .options(selectinload(QuestionOpinion.user))
    )
    opinion = result.scalar_one_or_none()
    if opinion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opinion not found")
    if opinion.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot edit another user's opinion")

    opinion.body = body.body
    await session.commit()
    await session.refresh(opinion)
    return _to_response(opinion)


@router.delete("/opinions/{opinion_id}", status_code=204)
async def delete_opinion(
    opinion_id: uuid.UUID,
    current_user: Annotated[User, Depends(require_user)],
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(QuestionOpinion).where(QuestionOpinion.id == opinion_id)
    )
    opinion = result.scalar_one_or_none()
    if opinion is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opinion not found")
    if opinion.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot delete another user's opinion")

    await session.delete(opinion)
    await session.commit()


def _to_response(opinion: QuestionOpinion) -> OpinionResponse:
    from app.schemas import UserResponse
    return OpinionResponse(
        id=opinion.id,
        question_id=opinion.question_id,
        user_id=opinion.user_id,
        author=UserResponse.model_validate(opinion.user),
        target=opinion.target,
        body=opinion.body,
        created_at=opinion.created_at,
        updated_at=opinion.updated_at,
    )
