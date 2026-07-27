import uuid

from fastapi import APIRouter, Depends, Query

from ... import audit
from ...deps import get_session, require_roles
from ...schemas import AuditLogOut
from . import repository as repo

router = APIRouter(prefix="/audit-log", tags=["audit"])


# Declared before the list route's siblings so the literal path wins the match.
@router.get(
    "/actions",
    response_model=list[str],
    dependencies=[Depends(require_roles("admin"))],
)
async def list_actions() -> list[str]:
    """The action vocabulary the log can be filtered by.

    Served rather than duplicated in the client: the filter list was previously
    a hand-maintained copy of `audit.ACTIONS`, and it went stale the first time
    an action was added — the new one was recorded but could not be filtered
    for.
    """
    return sorted(audit.ACTIONS)


@router.get("", response_model=list[AuditLogOut], dependencies=[Depends(require_roles("admin"))])
async def list_audit_log(
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    action: str | None = None,
    id_actor: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session=Depends(get_session),
) -> list[AuditLogOut]:
    """Admin-only action history, newest first.

    Paginated by necessity, not preference: unlike the other list endpoints,
    this table grows without bound.
    """
    rows = await repo.list_audit(
        session,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        id_actor=id_actor,
        limit=limit,
        offset=offset,
    )
    return [AuditLogOut.model_validate(r) for r in rows]
