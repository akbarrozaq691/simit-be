"""SQLAlchemy ORM models — one class per table in db/schema.sql.

UUID PKs use the same server-side `gen_random_uuid()` default as the schema
(pgcrypto), so INSERTs let Postgres generate the id and SQLAlchemy reads it
back via RETURNING.
"""

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

ArticleStatusEnum = PGEnum(
    "submitted",
    "assigned_to_sc",
    "under_review",
    "revision_needed",
    "passed_review",
    "announced",
    "full_paper_submitted",
    "rejected",
    "completed",
    name="article_status",
    create_type=False,  # already created by db/schema.sql
)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class Role(Base):
    __tablename__ = "role"

    id_role: Mapped[uuid.UUID] = _uuid_pk()
    name_role: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)


class Occupation(Base):
    __tablename__ = "occupation"

    id_occupation: Mapped[uuid.UUID] = _uuid_pk()
    occupation_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)


class MainTopic(Base):
    __tablename__ = "main_topic"

    id_topic: Mapped[uuid.UUID] = _uuid_pk()
    topic_name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)


class SubTopicStem(Base):
    __tablename__ = "sub_topic_stem"
    __table_args__ = (UniqueConstraint("id_topic", "stem_topic"),)

    id_stem: Mapped[uuid.UUID] = _uuid_pk()
    stem_topic: Mapped[str] = mapped_column(String(150), nullable=False)
    id_topic: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("main_topic.id_topic", ondelete="CASCADE"), nullable=False
    )


class SubTopicHumanity(Base):
    __tablename__ = "sub_topic_humanity"
    __table_args__ = (UniqueConstraint("id_topic", "humanity_topic"),)

    id_humanity: Mapped[uuid.UUID] = _uuid_pk()
    humanity_topic: Mapped[str] = mapped_column(String(150), nullable=False)
    id_topic: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("main_topic.id_topic", ondelete="CASCADE"), nullable=False
    )


class SubTopicInterdisciplinary(Base):
    __tablename__ = "sub_topic_interdisciplinary"
    __table_args__ = (UniqueConstraint("id_topic", "interdisciplinary_topic"),)

    id_interdisciplinary: Mapped[uuid.UUID] = _uuid_pk()
    interdisciplinary_topic: Mapped[str] = mapped_column(String(150), nullable=False)
    id_topic: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("main_topic.id_topic", ondelete="CASCADE"), nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id_user: Mapped[uuid.UUID] = _uuid_pk()
    user_name: Mapped[str] = mapped_column(String(150), nullable=False)
    institution_name: Mapped[str | None] = mapped_column(String(200))
    id_occupation: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("occupation.id_occupation")
    )
    id_role: Mapped[uuid.UUID] = mapped_column(ForeignKey("role.id_role"), nullable=False)
    email: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(30))
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )

    role: Mapped[Role] = relationship(lazy="joined")
    occupation: Mapped[Occupation | None] = relationship(lazy="joined")


class Journal(Base):
    __tablename__ = "journal"

    id_journal: Mapped[uuid.UUID] = _uuid_pk()
    journal_name: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = ()

    id_article: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    authors: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[str | None] = mapped_column(String(300))
    abstract_file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    full_paper_file_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(ArticleStatusEnum, server_default="submitted")

    id_user: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id_user"), nullable=False)
    id_sc: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id_user"))
    id_topic: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("main_topic.id_topic"))
    id_recommended_journal: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal.id_journal")
    )

    sc_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )


class Timeline(Base):
    __tablename__ = "timeline"
    __table_args__ = (CheckConstraint("end_date >= start_date"),)

    id_timeline: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[dt.datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    end_date: Mapped[dt.datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()")
    )
