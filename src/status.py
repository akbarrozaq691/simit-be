"""Internal pipeline status vs. what the author is allowed to see.

EIC/SC/admin always see the real `article_status` enum value, including the
internal `assigned_to_sc`, `abstract_review_complete`, `full_paper_submitted`,
and `full_paper_review_complete` states. Authors only ever see: submitted,
under_review, abstract_accepted, revision_needed, accepted, rejected.
"""

AUTHOR_STATUS_MAP = {
    "submitted": "submitted",
    "assigned_to_sc": "under_review",
    "abstract_review_complete": "under_review",
    "abstract_accepted": "abstract_accepted",
    "rejected": "rejected",
    "full_paper_submitted": "under_review",
    "full_paper_review_complete": "under_review",
    "revision_needed": "revision_needed",
    "accepted": "accepted",
}


def to_author_view(article: dict) -> dict:
    view = dict(article)
    view["status"] = AUTHOR_STATUS_MAP.get(article["status"], article["status"])
    return view


def apply_role_view(article: dict, role: str) -> dict:
    if role == "author":
        return to_author_view(article)
    return dict(article)
