"""The audit action vocabulary is a fixed, deliberate list. These tests pin it
so a typo in a handler's action string fails loudly instead of silently
writing an unqueryable row."""

import pytest

from src import audit

EXPECTED_ACTIONS = {
    "article.created",
    "article.status_changed",
    "article.deleted",
    "article.restored",
    "article.version_submitted",
    "article.file_downloaded",
    "reviewer.assigned",
    "reviewer.unassigned",
    "review.submitted",
    "user.created",
    "user.deleted",
    "user.restored",
}


def test_action_vocabulary_is_exactly_the_agreed_set():
    assert audit.ACTIONS == EXPECTED_ACTIONS


@pytest.mark.parametrize("action", sorted(EXPECTED_ACTIONS))
def test_every_action_is_recognised(action):
    assert audit.is_known_action(action) is True


def test_unknown_action_is_rejected():
    assert audit.is_known_action("article.mystery") is False


def test_entity_types_are_constrained():
    assert audit.ENTITY_TYPES == {"article", "user"}


def test_download_action_is_part_of_the_vocabulary():
    """Recorded but unfilterable is the failure this guards: the action was
    added to the writer without reaching the log's filter list."""
    assert audit.is_known_action("article.file_downloaded")
