"""Notification wording.

These are the only messages most authors read from SIMIT. The bug this guards
against already shipped once: the decision email said
`Your article status is now: revision_needed.`
"""

import pytest

from src import notifications
from src.status import AUTHOR_STATUS_MAP

TITLE = "Pemanfaatan Katalis Heterogen untuk Konversi Biomassa"

# Every internal state an author can trigger a notification from.
INTERNAL_STATUSES = sorted(AUTHOR_STATUS_MAP)


@pytest.mark.parametrize("internal", INTERNAL_STATUSES)
def test_no_status_slug_ever_reaches_the_author(internal):
    """Not the internal value, and not the masked one either — both are enum
    spellings, neither is English."""
    subject, body = notifications.author_status_change(TITLE, internal)
    masked = AUTHOR_STATUS_MAP[internal]
    for slug in {internal, masked}:
        if "_" in slug:
            assert slug not in subject
            assert slug not in body


@pytest.mark.parametrize("internal", INTERNAL_STATUSES)
def test_every_status_has_real_wording(internal):
    subject, body = notifications.author_status_change(TITLE, internal)
    assert subject and not subject.endswith(":")
    assert TITLE in body
    assert len(body) > 80


@pytest.mark.parametrize(
    "internal,expected_masked",
    [("assigned_to_sc", "under_review"), ("abstract_review_complete", "under_review"),
     ("full_paper_submitted", "under_review"), ("full_paper_review_complete", "under_review")],
)
def test_internal_phases_all_read_as_under_review(internal, expected_masked):
    """An author must not be able to tell which internal phase they are in, so
    every masked-to-under_review state produces the same message."""
    assert AUTHOR_STATUS_MAP[internal] == expected_masked
    assert notifications.author_status_change(TITLE, internal) == notifications.author_status_change(
        TITLE, "assigned_to_sc"
    )


def test_accept_and_reject_are_not_confusable():
    accepted, _ = notifications.author_status_change(TITLE, "accepted")
    rejected, _ = notifications.author_status_change(TITLE, "rejected")
    assert accepted != rejected
    assert "accepted" in accepted.lower()


def test_rejection_does_not_say_congratulations():
    _, body = notifications.author_status_change(TITLE, "rejected")
    assert "congratulation" not in body.lower()


def test_unknown_status_falls_back_without_leaking_it():
    """A status added to the enum but not here must not print its own name."""
    subject, body = notifications.author_status_change(TITLE, "some_future_state")
    assert "some_future_state" not in subject
    assert "some_future_state" not in body
    assert TITLE in body


@pytest.mark.parametrize(
    "factory",
    [
        notifications.author_abstract_received,
        notifications.author_full_paper_received,
        notifications.author_revision_received,
        notifications.reviewer_assigned,
    ],
)
def test_single_argument_messages_name_the_submission(factory):
    subject, body = factory(TITLE)
    assert subject
    assert TITLE in body


def test_revised_and_first_submission_read_differently_to_a_reviewer():
    first, _ = notifications.reviewer_full_paper_ready(TITLE, revised=False)
    revised, _ = notifications.reviewer_full_paper_ready(TITLE, revised=True)
    assert "revised" in revised.lower()
    assert "revised" not in first.lower()


@pytest.mark.parametrize("phase,expected", [("abstract", "abstract"), ("full_paper", "full paper")])
def test_editor_notice_names_the_stage_in_words(phase, expected):
    subject, body = notifications.editor_reviews_complete(TITLE, phase)
    assert expected in subject
    assert "full_paper" not in subject and "full_paper" not in body
    assert TITLE in body
