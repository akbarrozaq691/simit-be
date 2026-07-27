"""The wording of every pipeline notification, in one place.

Kept out of the router because these are the only messages most authors will
ever read from SIMIT, and they were previously assembled inline — which is how
`Your article status is now: revision_needed.` reached a human being.

Each function returns `(subject, body)` for `emailer.send`. Bodies are plain
text; the HTML rendering in `email_template` wraps whatever is returned here.
"""

from .status import AUTHOR_STATUS_MAP

# What each author-visible status means, in words an author can act on. Keyed by
# the masked status, so an internal state can never leak through.
_AUTHOR_STATUS_WORDING: dict[str, tuple[str, str]] = {
    "submitted": (
        "Submission received",
        "Your submission has been recorded and is waiting to be assigned to reviewers.",
    ),
    "under_review": (
        "Your submission is under review",
        "Reviewers are now reading your submission. We will write again as soon as there is a decision.",
    ),
    "abstract_accepted": (
        "Your abstract has been accepted",
        "The reviewers have accepted your abstract. The next step is the full paper: sign in to the "
        "portal and upload it before the deadline on the schedule.",
    ),
    "revision_needed": (
        "Revision requested for your full paper",
        "The reviewers have asked for a revision before a final decision can be made. Sign in to the "
        "portal to upload the revised version.",
    ),
    "accepted": (
        "Your paper has been accepted",
        "Congratulations — your paper has been accepted. Sign in to the portal to see the recommended "
        "journal and any further instructions.",
    ),
    "rejected": (
        "Decision on your submission",
        "After review, your submission has not been accepted for this symposium. We are grateful for "
        "the work you put into it and hope to see you submit again.",
    ),
}

_FALLBACK = (
    "Update on your submission",
    "The status of your submission has changed. Sign in to the portal to see the details.",
)


def _titled(title: str, sentence: str) -> str:
    return f'Regarding your submission "{title}":\n\n{sentence}'


# ---- to the author ----


def author_status_change(title: str, internal_status: str) -> tuple[str, str]:
    """Announced decisions and phase changes.

    The status is masked first: authors are not shown internal states such as
    `assigned_to_sc`, and they must never be shown the enum value itself.
    """
    masked = AUTHOR_STATUS_MAP.get(internal_status, internal_status)
    subject, sentence = _AUTHOR_STATUS_WORDING.get(masked, _FALLBACK)
    return subject, _titled(title, sentence)


def author_abstract_received(title: str) -> tuple[str, str]:
    return (
        "We have received your abstract",
        _titled(
            title,
            "Your abstract has been submitted successfully. It will be assigned to reviewers, and you "
            "will hear from us at each step. No action is needed from you for now.",
        ),
    )


def author_full_paper_received(title: str) -> tuple[str, str]:
    return (
        "We have received your full paper",
        _titled(
            title,
            "Your full paper has been submitted successfully and is now with the reviewers.",
        ),
    )


def author_revision_received(title: str) -> tuple[str, str]:
    return (
        "We have received your revised paper",
        _titled(
            title,
            "Your revised full paper has been submitted successfully and has gone back to the "
            "reviewers.",
        ),
    )


# ---- to reviewers ----


def reviewer_assigned(title: str) -> tuple[str, str]:
    return (
        "A submission has been assigned to you for review",
        f'"{title}" has been assigned to you for review. Sign in to the portal to read it and submit '
        "your assessment.",
    )


def reviewer_full_paper_ready(title: str, *, revised: bool) -> tuple[str, str]:
    what = "revised full paper" if revised else "full paper"
    return (
        f"A {what} is ready for your review",
        f'The {what} for "{title}" is ready for your review. Sign in to the portal to read it and '
        "submit your assessment.",
    )


# ---- to editors ----


def editor_reviews_complete(title: str, phase: str) -> tuple[str, str]:
    """Sent once every assigned reviewer has submitted for the current phase.

    Without it an editor has to keep opening the dashboard to find out whether a
    decision can be announced.
    """
    stage = "abstract" if phase == "abstract" else "full paper"
    return (
        f"All {stage} reviews are in",
        f'Every assigned reviewer has submitted their {stage} review for "{title}". A decision can now '
        "be announced.",
    )
