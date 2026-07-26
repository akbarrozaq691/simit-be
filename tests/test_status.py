import pytest

from src.status import AUTHOR_STATUS_MAP, to_author_view

ALL_STATUSES = [
    "submitted",
    "assigned_to_sc",
    "abstract_review_complete",
    "abstract_accepted",
    "rejected",
    "full_paper_submitted",
    "full_paper_review_complete",
    "revision_needed",
    "accepted",
]


@pytest.mark.parametrize("real_status", ALL_STATUSES)
def test_every_real_status_has_an_author_mapping(real_status):
    assert real_status in AUTHOR_STATUS_MAP


def test_internal_states_hidden_as_under_review():
    for internal in (
        "assigned_to_sc",
        "abstract_review_complete",
        "full_paper_submitted",
        "full_paper_review_complete",
    ):
        assert AUTHOR_STATUS_MAP[internal] == "under_review"


def test_terminal_and_actionable_states_pass_through():
    assert AUTHOR_STATUS_MAP["abstract_accepted"] == "abstract_accepted"
    assert AUTHOR_STATUS_MAP["rejected"] == "rejected"
    assert AUTHOR_STATUS_MAP["revision_needed"] == "revision_needed"
    assert AUTHOR_STATUS_MAP["accepted"] == "accepted"


def test_to_author_view_maps_status_field():
    article = {"status": "assigned_to_sc", "title": "x"}
    assert to_author_view(article)["status"] == "under_review"
