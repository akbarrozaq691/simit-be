"""Internal pipeline status vs. what the author is allowed to see.

EIC/SC/admin always see the real `article_status` enum value. Authors only
see a coarser view — e.g. `assigned_to_sc`, `under_review`, `revision_needed`
and `passed_review` are all internal review-stage detail; to the author it's
just "under_review" until EIC announces a decision.
"""

AUTHOR_STATUS_MAP = {
    "submitted": "submitted",
    "assigned_to_sc": "under_review",
    "under_review": "under_review",
    "revision_needed": "under_review",
    "passed_review": "under_review",
    "announced": "abstract_accepted",
    "full_paper_submitted": "under_review",
    "rejected": "rejected",
    "completed": "completed",
}


def to_author_view(article: dict) -> dict:
    view = dict(article)
    view["status"] = AUTHOR_STATUS_MAP.get(article["status"], article["status"])
    return view


def apply_role_view(article: dict, role: str) -> dict:
    if role == "author":
        return to_author_view(article)
    return dict(article)
