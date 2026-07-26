"""Pydantic request/response schemas for every resource. Kept in one file for a
compact layout — split per-domain if this grows past a few hundred lines."""

import datetime as dt
import uuid
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Plain "user@domain.tld" shape check — deliberately not pydantic.EmailStr,
# which rejects RFC 6761 special-use domains like `.local` that dev/seed
# accounts here use (admin@simit.local, etc).
EmailStr = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
]


class ORMModel(BaseModel):
    """Base for response schemas built from SQLAlchemy ORM instances."""

    model_config = ConfigDict(from_attributes=True)


class RoleName(str, Enum):
    admin = "admin"
    eic = "EIC"
    sc = "SC"
    author = "author"


class ArticleStatus(str, Enum):
    submitted = "submitted"
    assigned_to_sc = "assigned_to_sc"
    abstract_review_complete = "abstract_review_complete"
    abstract_accepted = "abstract_accepted"
    rejected = "rejected"
    full_paper_submitted = "full_paper_submitted"
    full_paper_review_complete = "full_paper_review_complete"
    revision_needed = "revision_needed"
    accepted = "accepted"


class UserCtx(BaseModel):
    """Decoded JWT identity, injected via Depends(get_current_user)."""

    id_user: str
    role: str


# ---- Auth ----

class RegisterRequest(BaseModel):
    user_name: str
    email: EmailStr
    password: str = Field(min_length=6)
    occupation_name: str
    institution_name: str | None = None
    phone_number: str | None = None


class AuthUserOut(ORMModel):
    id_user: uuid.UUID
    user_name: str
    email: EmailStr
    role: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenOut(ORMModel):
    access_token: str
    token_type: str = "Bearer"
    id_user: uuid.UUID
    user_name: str
    role: str


# ---- Users ----

class UserCreate(BaseModel):
    user_name: str
    email: EmailStr
    password: str = Field(min_length=6)
    name_role: RoleName
    institution_name: str | None = None
    phone_number: str | None = None
    occupation_name: str | None = None


class UserUpdate(BaseModel):
    user_name: str | None = None
    institution_name: str | None = None
    phone_number: str | None = None
    password: str | None = Field(default=None, min_length=6)


class UserOut(ORMModel):
    id_user: uuid.UUID
    user_name: str
    institution_name: str | None
    email: EmailStr
    phone_number: str | None
    created_at: dt.datetime
    role: str
    occupation_name: str | None = None


# ---- Reference data: role / occupation / journal ----

class RoleCreate(BaseModel):
    name_role: str


class RoleOut(ORMModel):
    id_role: uuid.UUID
    name_role: str


class OccupationCreate(BaseModel):
    occupation_name: str


class OccupationOut(ORMModel):
    id_occupation: uuid.UUID
    occupation_name: str


class JournalCreate(BaseModel):
    journal_name: str


class JournalOut(ORMModel):
    id_journal: uuid.UUID
    journal_name: str


# ---- Timeline ----

class TimelineCreate(BaseModel):
    title: str
    description: str | None = None
    start_date: dt.datetime
    end_date: dt.datetime

    @model_validator(mode="after")
    def _check_date_range(self) -> "TimelineCreate":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be >= start_date")
        return self


class TimelineUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    start_date: dt.datetime | None = None
    end_date: dt.datetime | None = None


class TimelineOut(ORMModel):
    id_timeline: uuid.UUID
    title: str
    description: str | None
    start_date: dt.datetime
    end_date: dt.datetime
    created_at: dt.datetime


# ---- Topics ----

class TopicCreate(BaseModel):
    topic_name: str


class TopicOut(ORMModel):
    id_topic: uuid.UUID
    topic_name: str


class SubTopicCreate(BaseModel):
    id_topic: uuid.UUID
    name: str


class SubTopicOut(ORMModel):
    id_sub_topic: uuid.UUID
    name: str
    id_topic: uuid.UUID


class TopicWithSubtopics(TopicOut):
    stem: list[SubTopicOut] = []
    humanity: list[SubTopicOut] = []
    interdisciplinary: list[SubTopicOut] = []


# ---- Articles ----

class ArticleCreate(BaseModel):
    title: str
    authors: str
    abstract: str
    keywords: str | None = None
    abstract_file_path: str
    id_topic: uuid.UUID | None = None


class ArticleUpdate(BaseModel):
    title: str | None = None
    authors: str | None = None
    abstract: str | None = None
    keywords: str | None = None
    abstract_file_path: str | None = None
    id_topic: uuid.UUID | None = None


class ArticleOut(ORMModel):
    id_article: uuid.UUID
    title: str
    authors: str
    abstract: str
    keywords: str | None
    abstract_file_path: str
    full_paper_file_path: str | None
    status: str
    id_user: uuid.UUID
    id_topic: uuid.UUID | None
    id_recommended_journal: uuid.UUID | None
    reviewers: list[uuid.UUID] = []
    created_at: dt.datetime
    updated_at: dt.datetime


class ArticleAssignRequest(BaseModel):
    id_sc: uuid.UUID
    override_coi: bool = False


class AssignReviewersRequest(BaseModel):
    id_reviewers: list[uuid.UUID] = Field(min_length=1)
    override_coi: bool = False


class AbstractAnnounceRequest(BaseModel):
    decision: Literal["accept", "reject"]


class FullPaperAnnounceRequest(BaseModel):
    decision: Literal["accept", "revision"]
    id_recommended_journal: uuid.UUID | None = None

    @model_validator(mode="after")
    def _journal_required_on_accept(self) -> "FullPaperAnnounceRequest":
        if self.decision == "accept" and self.id_recommended_journal is None:
            raise ValueError("id_recommended_journal is required when decision is 'accept'")
        return self


class ArticleReviewOut(ORMModel):
    id_review: uuid.UUID
    id_version: uuid.UUID
    id_reviewer: uuid.UUID
    decision: str
    notes: str | None
    reviewed_at: dt.datetime


class AbstractReviewRequest(BaseModel):
    accept: bool
    notes: str | None = None


class FullPaperReviewRequest(BaseModel):
    decision: Literal["accept", "revision"]
    notes: str | None = None
    id_recommended_journal: uuid.UUID | None = None


class ArticleVersionOut(ORMModel):
    id_version: uuid.UUID
    id_article: uuid.UUID
    phase: str
    version_number: int
    file_path: str
    submitted_by: uuid.UUID
    submitted_at: dt.datetime


class UploadResponse(BaseModel):
    file_path: str


class ArticleFullPaperRequest(BaseModel):
    full_paper_file_path: str
