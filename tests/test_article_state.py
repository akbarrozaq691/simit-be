import pytest

from src import article_state


def test_abstract_reviewable_set():
    assert article_state.ABSTRACT_REVIEWABLE == {"assigned_to_sc"}


def test_full_paper_reviewable_set():
    assert article_state.FULL_PAPER_REVIEWABLE == {"full_paper_submitted"}


# ---- COI ----


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("Universitas Indonesia", "Universitas Indonesia", True),
        ("universitas indonesia", "UNIVERSITAS INDONESIA", True),
        ("  Universitas Indonesia  ", "Universitas Indonesia", True),
        ("Universitas Indonesia", "Institut Teknologi Bandung", False),
        (None, "Universitas Indonesia", False),
        ("Universitas Indonesia", None, False),
        (None, None, False),
        ("", "Universitas Indonesia", False),
        ("   ", "Universitas Indonesia", False),
    ],
)
def test_institutions_conflict(a, b, expected):
    assert article_state.institutions_conflict(a, b) is expected


# ---- Review-complete status ----


def test_review_complete_status_abstract():
    assert article_state.review_complete_status_for_phase("abstract") == "abstract_review_complete"


def test_review_complete_status_full_paper():
    assert (
        article_state.review_complete_status_for_phase("full_paper")
        == "full_paper_review_complete"
    )


def test_review_complete_status_unknown_phase_raises():
    with pytest.raises(ValueError):
        article_state.review_complete_status_for_phase("nonsense")


# ---- Announce ----


@pytest.mark.parametrize(
    "phase,decision,expected",
    [
        ("abstract", "accept", "abstract_accepted"),
        ("abstract", "reject", "rejected"),
        ("full_paper", "accept", "accepted"),
        ("full_paper", "revision", "revision_needed"),
    ],
)
def test_announced_status_for(phase, decision, expected):
    assert article_state.announced_status_for(phase, decision) == expected


@pytest.mark.parametrize(
    "phase,decision",
    [
        ("abstract", "revision"),
        ("full_paper", "reject"),
        ("abstract", "nonsense"),
        ("nonsense", "accept"),
    ],
)
def test_announced_status_for_illegal_combination_raises(phase, decision):
    with pytest.raises(ValueError):
        article_state.announced_status_for(phase, decision)


def test_announceable_sets():
    assert article_state.ABSTRACT_ANNOUNCEABLE == {"abstract_review_complete"}
    assert article_state.FULL_PAPER_ANNOUNCEABLE == {"full_paper_review_complete"}
