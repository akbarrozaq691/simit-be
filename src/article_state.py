"""Pure status-transition logic for the article review pipeline.

No DB or HTTP dependencies — router handlers call these functions and then
persist the returned status. Kept separate so the state machine (the
highest-risk part of the review pipeline) is unit-testable on its own.
"""

ABSTRACT_REVIEWABLE = {"assigned_to_sc"}
FULL_PAPER_REVIEWABLE = {"full_paper_submitted"}

_ANNOUNCE_ABSTRACT_ACCEPT = "abstract_decided_accept"
_ANNOUNCE_ABSTRACT_REJECT = "abstract_decided_reject"
_ANNOUNCE_FULL_PAPER_REVISION = "full_paper_decided_revision"
_ANNOUNCE_FULL_PAPER_ACCEPT = "full_paper_decided_accept"

_ANNOUNCE_MAP = {
    _ANNOUNCE_ABSTRACT_ACCEPT: "abstract_accepted",
    _ANNOUNCE_ABSTRACT_REJECT: "rejected",
    _ANNOUNCE_FULL_PAPER_REVISION: "revision_needed",
    _ANNOUNCE_FULL_PAPER_ACCEPT: "accepted",
}


def decide_abstract_review(accept: bool) -> str:
    return _ANNOUNCE_ABSTRACT_ACCEPT if accept else _ANNOUNCE_ABSTRACT_REJECT


def decide_full_paper_review(decision: str) -> str:
    if decision == "accept":
        return _ANNOUNCE_FULL_PAPER_ACCEPT
    if decision == "revision":
        return _ANNOUNCE_FULL_PAPER_REVISION
    raise ValueError(f"unknown decision: {decision!r}")


def announce_result(current_status: str) -> str:
    if current_status not in _ANNOUNCE_MAP:
        raise ValueError(f"cannot announce from status: {current_status!r}")
    return _ANNOUNCE_MAP[current_status]
