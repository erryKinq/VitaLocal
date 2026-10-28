"""AI coach in the training area: debriefs of single workouts and training plans from a questionnaire."""

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Connection

from vitalocal import ai, coach, coach_plan, coach_review, training
from vitalocal.db import CONN

router = APIRouter(prefix="/api/v1/coach", tags=["coach"])


@router.get("/status")
def get_status(conn: Connection = CONN) -> dict:
    """Whether the coach can be used (an AI model is set up and shared) and which model answers."""
    s = ai.load_settings(conn)
    on = ai.available(s, "coach")
    return {"enabled": on, "model": s.model if on else None}


class DebriefIn(BaseModel):
    activity_id: int
    lang: Literal["de", "en"] = "de"


@router.post("/debrief", status_code=status.HTTP_202_ACCEPTED)
def create_debrief(body: DebriefIn, conn: Connection = CONN) -> dict:
    """Starts the debrief of one workout; the answer arrives later (`status` pending → done/error,
    poll `GET /coach/messages/{id}`): `summary`, `score` 1–10, `highlights`, `watch`, `next_time`."""
    try:
        return coach.start_debrief(conn, body.activity_id, body.lang)
    except coach.CoachError as e:
        raise HTTPException(409, str(e)) from e


@router.get("/messages")
def get_messages(
    activity_id: int | None = Query(None, description="Only debriefs of this workout"),
    kind: Literal["debrief", "plan", "review"] | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    conn: Connection = CONN,
) -> list[dict]:
    """The coach's answers, newest first (pending and failed ones included)."""
    return coach.messages(conn, activity_id, limit, kind)


class PlanAnswersIn(BaseModel):
    goal: Literal["muscle", "strength", "fitness", "fat_loss"]
    experience: Literal["beginner", "intermediate", "advanced"]
    days_per_week: int = Field(ge=1, le=7)
    minutes: int = Field(ge=20, le=150, description="Per session")
    equipment: list[str] = Field(
        default_factory=list, description="Keys of /training/equipment; none: body weight"
    )
    limitations: str = Field("", max_length=500, description="Injuries or complaints to work around")
    focus: list[str] = Field(
        default_factory=list, max_length=12, description="Muscles to give more volume (`/training/muscles`)"
    )
    likes: str = Field("", max_length=300)
    dislikes: str = Field("", max_length=300)
    cardio: bool = False

    @model_validator(mode="after")
    def _check(self):
        for x in self.equipment:
            if x not in training.EQUIPMENT:
                raise ValueError(f"unknown equipment {x!r}")
        self.focus = training.expand(self.focus)[0]
        for m in self.focus:
            if m not in training.MUSCLES:
                raise ValueError(f"unknown muscle {m!r}")
        return self


class PlanRequestIn(BaseModel):
    answers: PlanAnswersIn
    lang: Literal["de", "en"] = "de"
    refines: uuid.UUID | None = Field(None, description="A finished proposal to revise")
    change: str | None = Field(None, max_length=1000, description="What to change in the refined proposal")


@router.get("/profile")
def get_profile(conn: Connection = CONN) -> dict | None:
    """The questionnaire's last answers (null before the first plan request)."""
    return coach_plan.load_answers(conn)


@router.post("/plan", status_code=status.HTTP_202_ACCEPTED)
def create_plan(body: PlanRequestIn, conn: Connection = CONN) -> dict:
    """Starts a plan proposal from the answers (they are saved as the profile), or revises a finished
    proposal (`refines` + `change`). The proposal arrives as a coach message of kind `plan`: `name`,
    `summary`, `days` (rotation order, exercises with sets, rep range or seconds, rest, names, minutes
    per day), `notes`, `hard_sets_per_week` per muscle."""
    if body.refines and not (body.change or "").strip():
        raise HTTPException(422, "say what to change")
    try:
        return coach_plan.start_plan(conn, body.answers.model_dump(), body.lang, body.refines, body.change)
    except coach.CoachError as e:
        raise HTTPException(409, str(e)) from e


class ReviewIn(BaseModel):
    note: str | None = Field(
        None, max_length=1000, description="What the person wants the review to consider"
    )
    lang: Literal["de", "en"] = "de"


@router.post("/review", status_code=status.HTTP_202_ACCEPTED)
def create_review(body: ReviewIn, conn: Connection = CONN) -> dict:
    """Starts a review of the active plan against the last 12 weeks. The answer (kind `review`):
    `nochange`, `reading`, `changes` (closed list of types, each with `why`, `where`, `before`,
    `after`), `notes`. Weights and targets are never changed."""
    try:
        return coach_review.start_review(conn, body.lang, body.note)
    except coach.CoachError as e:
        raise HTTPException(409, str(e)) from e


class TakeOverIn(BaseModel):
    changes: list[int] = Field(min_length=1, max_length=6, description="Indexes of the changes to make")


@router.post("/messages/{mid}/take-over")
def take_over(mid: uuid.UUID, body: TakeOverIn, conn: Connection = CONN) -> dict:
    """Makes the chosen changes of a review in the plan (refused when the plan changed since the
    review); the plan as it was is kept for undo."""
    try:
        return coach_review.take_over(conn, mid, body.changes)
    except coach.CoachError as e:
        raise HTTPException(409, str(e)) from e


@router.post("/messages/{mid}/undo")
def undo(mid: uuid.UUID, conn: Connection = CONN) -> dict:
    """Puts the plan back as it was before a review's changes were taken over."""
    try:
        return coach_review.undo(conn, mid)
    except coach.CoachError as e:
        raise HTTPException(409, str(e)) from e


class ApplyIn(BaseModel):
    active: bool = Field(False, description="Make it the active plan (its rotation is suggested)")


@router.post("/messages/{mid}/apply", status_code=status.HTTP_201_CREATED)
def apply_plan(mid: uuid.UUID, body: ApplyIn, conn: Connection = CONN) -> dict:
    """Creates the proposal as a plan; library exercises become own exercises. Weights and targets
    follow the progression from the first session on."""
    try:
        return coach_plan.apply(conn, mid, body.active)
    except coach.CoachError as e:
        raise HTTPException(409, str(e)) from e


@router.delete("/messages/{mid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(mid: uuid.UUID, conn: Connection = CONN) -> Response:
    """Discards a proposal or debrief; a plan already taken over stays."""
    if not coach.delete(conn, mid):
        raise HTTPException(404, "message not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/messages/{mid}")
def get_message(mid: uuid.UUID, conn: Connection = CONN) -> dict:
    m = coach.message(conn, mid)
    if m is None:
        raise HTTPException(404, "message not found")
    return m
