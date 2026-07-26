import pytest

from src import article_state


def test_decide_abstract_review_accept():
    assert article_state.decide_abstract_review(True) == "abstract_decided_accept"


def test_decide_abstract_review_reject():
    assert article_state.decide_abstract_review(False) == "abstract_decided_reject"


def test_decide_full_paper_review_accept():
    assert article_state.decide_full_paper_review("accept") == "full_paper_decided_accept"


def test_decide_full_paper_review_revision():
    assert article_state.decide_full_paper_review("revision") == "full_paper_decided_revision"


def test_decide_full_paper_review_unknown_decision_raises():
    with pytest.raises(ValueError):
        article_state.decide_full_paper_review("maybe")


@pytest.mark.parametrize(
    "decided_status,expected",
    [
        ("abstract_decided_accept", "abstract_accepted"),
        ("abstract_decided_reject", "rejected"),
        ("full_paper_decided_revision", "revision_needed"),
        ("full_paper_decided_accept", "accepted"),
    ],
)
def test_announce_result(decided_status, expected):
    assert article_state.announce_result(decided_status) == expected


def test_announce_result_not_announceable_raises():
    with pytest.raises(ValueError):
        article_state.announce_result("submitted")


def test_abstract_reviewable_set():
    assert article_state.ABSTRACT_REVIEWABLE == {"assigned_to_sc"}


def test_full_paper_reviewable_set():
    assert article_state.FULL_PAPER_REVIEWABLE == {"full_paper_submitted"}
