# Audit Log and Soft Delete

Date: 2026-07-27
Status: approved (design)

## Context

Two remaining Scope-B features, specified together because they are two halves
of the same concern — data traceability. An audit log records *who did what*;
soft delete preserves *what was removed*. They also share one migration and
one set of touched handlers, so splitting them would mean two migrations over
the same tables.

Current state after the multi-reviewer work: no action history exists at all,
and `DELETE` on articles and users is a hard delete. The user-delete path is
additionally broken — deleting a user who is referenced as an article's author
or reviewer raises a raw `ForeignKeyViolationError` surfaced as **500** (found
during multi-reviewer verification). Soft delete fixes that as a side effect.

## Product Decisions (settled with the user)

- **Audit scope: significant events only**, written explicitly at each
  handler. Not a catch-all middleware over every mutating request — that
  would capture noise and risk logging sensitive payloads (passwords).
- **No before/after value capture.** Events carry a small structured detail
  (e.g. status `from`→`to`), not full row snapshots.
- **Audit log is admin-only.** It records every actor's actions including the
  EIC's, so the EIC cannot read it.
- **Soft delete covers articles and users.** Related data (versions, reviews,
  assignments) is left intact, not cascaded.

## Audit Log

### Table

```sql
CREATE TABLE audit_log (
    id_audit      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    id_actor      UUID REFERENCES users(id_user),   -- NULL for unauthenticated/system actions
    action        VARCHAR(50) NOT NULL,
    entity_type   VARCHAR(30) NOT NULL,
    entity_id     UUID,
    detail        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_audit_log_created_at ON audit_log(created_at DESC);
CREATE INDEX idx_audit_log_entity ON audit_log(entity_type, entity_id);
CREATE INDEX idx_audit_log_actor ON audit_log(id_actor);
```

`id_actor` is nullable and **not** `ON DELETE CASCADE`: an audit trail that
disappears when its actor is removed is not an audit trail. Combined with soft
delete, actors normally remain resolvable anyway.

`detail` is `JSONB` because event shapes differ — a status change carries
`{"from": ..., "to": ...}`, an assignment carries `{"id_reviewer": ...}`.
Fixed columns for each would be mostly NULL.

### Recorded events

| action | entity_type | detail |
|---|---|---|
| `article.created` | article | `{"title": ...}` |
| `article.status_changed` | article | `{"from": ..., "to": ...}` |
| `article.deleted` | article | `{}` |
| `article.restored` | article | `{}` |
| `article.version_submitted` | article | `{"phase": ..., "version_number": ...}` |
| `reviewer.assigned` | article | `{"id_reviewer": ..., "coi_overridden": bool}` |
| `reviewer.unassigned` | article | `{"id_reviewer": ...}` |
| `review.submitted` | article | `{"id_reviewer": ..., "id_version": ..., "decision": ...}` |
| `user.created` | user | `{"role": ...}` |
| `user.deleted` | user | `{}` |
| `user.restored` | user | `{}` |

`entity_type` is `article` even for reviewer/review events: the article is the
thing an auditor searches by, and `entity_id` pointing at the article makes
"show me everything that happened to this paper" one query.

Whether a COI was overridden is recorded deliberately — it is exactly the kind
of discretionary act an audit trail exists for.

### Implementation

A single helper, called explicitly:

```python
async def record(session, *, id_actor, action, entity_type, entity_id, detail=None) -> None
```

Explicit calls over middleware: the events above are a deliberate list, and
inserting at the handler means the audit row lands in the same transaction as
the change it describes. If the request fails and rolls back, no phantom audit
entry survives.

Writes are best-effort in the sense that they must never be the reason a
request fails — but since they share the transaction, a failing audit insert
would roll back the whole operation. That is the intended trade-off: an
unauditable change should not silently succeed.

### API

**`GET /audit-log`** (admin only) with optional filters `entity_type`,
`entity_id`, `action`, `id_actor`, and `limit` / `offset` pagination
(default limit 50, max 200). Newest first.

This is the first paginated list endpoint in the codebase. Pagination is
mandatory here rather than optional: unlike every other list, this table grows
without bound. (The pre-existing lack of pagination on the other list
endpoints was noted in an earlier review and remains a separate, deferred
concern.)

## Soft Delete

### Schema

`articles.deleted_at TIMESTAMPTZ NULL` and `users.deleted_at TIMESTAMPTZ NULL`.
NULL means live; a timestamp means archived.

Partial indexes keep the common "live rows only" filter cheap:

```sql
CREATE INDEX idx_articles_live ON articles(id_article) WHERE deleted_at IS NULL;
CREATE INDEX idx_users_live ON users(id_user) WHERE deleted_at IS NULL;
```

### Behavior

- `DELETE /articles/{id}` and `DELETE /users/{id}` set `deleted_at` instead of
  removing the row. Response stays **204**, so existing clients see no change.
  Deleting something already deleted is **404** (it is not a live resource).
- Related rows (`article_version`, `article_review`, `article_reviewer`) are
  **not** touched. Preserving the review record is the entire point.
- **List endpoints exclude archived rows.** `GET /articles` and `GET /users`
  gain `include_deleted: bool = False`; passing `true` is **admin-only**
  (403 otherwise).
- **Fetching an archived entity by id returns 404** for everyone except admin,
  who sees it with its `deleted_at` populated. An archived article cannot be
  advanced through the pipeline — every mutating article handler rejects a
  soft-deleted article with **404**, treating it as gone.
- `POST /articles/{id}/restore` and `POST /users/{id}/restore` (admin) clear
  `deleted_at`. Restoring a live entity is **409**.
- `ArticleOut` and `UserOut` gain `deleted_at: dt.datetime | None`.

### Security: archived users must lose access immediately

This is the part most likely to be missed. A JWT stays cryptographically valid
until it expires, so archiving a user does nothing on its own — their existing
token would keep working for up to `jwt_expire_minutes` (default 24h).

Two changes close it:

1. **`login` rejects archived users** with the same 401 as bad credentials
   (no "this account was deleted" disclosure).
2. **`get_current_user` verifies the actor is still live** on every request,
   returning 401 if their row is archived.

Change 2 adds one indexed primary-key lookup per authenticated request. That
is the accepted cost of revocation actually working; the current design has no
other revocation mechanism.

### Consequence: email addresses stay claimed

`users.email` is `UNIQUE`, and an archived user still holds their address, so
re-registering with it returns 409 "email already registered". This is
deliberate — silently freeing the address would let a new account inherit an
archived person's identity in the audit trail. Admin restores the account
instead.

## Migration (003)

1. Create `audit_log` with its three indexes.
2. Add `deleted_at` to `articles` and `users` (nullable — all existing rows are
   live).
3. Create the two partial indexes.

No backfill: there is no historical action data to reconstruct, and every
existing row is live by definition. Unlike migrations 001 and 002, this one is
purely additive and drops nothing.

## Testing

- **Unit-testable**: the audit `detail` builders (pure dict construction), and
  the `include_deleted` permission rule (admin vs non-admin).
- **Manual smoke test** against the dockerized stack for everything else, as
  with the previous three features: soft delete then 404, restore then
  reachable again, archived user's existing token rejected, archived user
  cannot log in, audit rows appear for each event type with the right actor,
  pagination bounds, and the previously-500 user-delete path now returning 204.
- The **user-delete FK bug** gets an explicit regression check: delete a user
  who authors an article and is assigned as a reviewer → expect 204, not 500.

## Out of Scope

- Purge/retention policy for `audit_log` (it grows forever; revisit when size
  warrants).
- Audit entries for reads.
- Cascading soft delete to reference data (topics, journals, occupations).
- Restoring individual reviews or versions independently of their article.
- Pagination for the pre-existing list endpoints — still deferred.
