"""Audit log writer.

Significant events only, recorded explicitly at the handler that performs the
change. The insert shares the caller's transaction, so an audit row can never
describe a change that was rolled back.

Deliberately NOT middleware: the event list below is a curated set, and
request-level interception would both capture noise and risk logging sensitive
payloads.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog

ACTIONS = frozenset(
    {
        "article.created",
        "article.status_changed",
        "article.deleted",
        "article.restored",
        "article.version_submitted",
        "reviewer.assigned",
        "reviewer.unassigned",
        "review.submitted",
        "user.created",
        "user.deleted",
        "user.restored",
    }
)

ENTITY_TYPES = frozenset({"article", "user"})


def is_known_action(action: str) -> bool:
    return action in ACTIONS


async def record(
    session: AsyncSession,
    *,
    id_actor: uuid.UUID | None,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None,
    detail: dict | None = None,
) -> None:
    """Appends an audit row. Raises ValueError on an unknown action or entity
    type — a mistyped action string would produce a row nobody can query, so
    failing loudly in development is better than logging garbage."""
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action: {action!r}")
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"unknown audit entity_type: {entity_type!r}")

    session.add(
        AuditLog(
            id_actor=id_actor,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail=detail,
        )
    )
    await session.flush()
