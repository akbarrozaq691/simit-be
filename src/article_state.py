"""Pure status-transition and policy logic for the article review pipeline.

No DB or HTTP dependencies — routers call these and then persist. Kept
separate so the riskiest logic (state transitions, conflict-of-interest
screening) is unit-testable on its own.

Decisions live per-reviewer in `article_review`; an article's status only
records how far the pipeline has advanced, not what any reviewer decided.
"""

ABSTRACT_REVIEWABLE = {"assigned_to_sc"}
FULL_PAPER_REVIEWABLE = {"full_paper_submitted"}

ABSTRACT_ANNOUNCEABLE = {"abstract_review_complete"}
FULL_PAPER_ANNOUNCEABLE = {"full_paper_review_complete"}

_REVIEW_COMPLETE_STATUS = {
    "abstract": "abstract_review_complete",
    "full_paper": "full_paper_review_complete",
}

_ANNOUNCED_STATUS = {
    ("abstract", "accept"): "abstract_accepted",
    ("abstract", "reject"): "rejected",
    ("full_paper", "accept"): "accepted",
    ("full_paper", "revision"): "revision_needed",
}


def institutions_conflict(a: str | None, b: str | None) -> bool:
    """True when two institution names indicate a conflict of interest.

    Compared case-insensitively after trimming. A missing or blank value on
    either side returns False: absence of evidence is not evidence of a
    conflict, and blocking on it would make incomplete profiles unassignable.
    Exact-match only — "Univ. Indonesia" will not match "Universitas
    Indonesia". A deliberate heuristic; the EIC override covers the rest.
    """
    if not a or not b:
        return False
    left = a.strip().casefold()
    right = b.strip().casefold()
    if not left or not right:
        return False
    return left == right


def review_complete_status_for_phase(phase: str) -> str:
    if phase not in _REVIEW_COMPLETE_STATUS:
        raise ValueError(f"unknown phase: {phase!r}")
    return _REVIEW_COMPLETE_STATUS[phase]


def announced_status_for(phase: str, decision: str) -> str:
    key = (phase, decision)
    if key not in _ANNOUNCED_STATUS:
        raise ValueError(f"illegal announce combination: phase={phase!r} decision={decision!r}")
    return _ANNOUNCED_STATUS[key]
