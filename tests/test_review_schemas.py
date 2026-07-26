"""The /review endpoint accepts two disjoint body shapes via a pydantic union.
These tests pin the discrimination behavior the endpoint relies on, plus the
OpenAPI schema it now publishes."""

import pytest
from pydantic import TypeAdapter, ValidationError

from src.main import app
from src.schemas import AbstractReviewRequest, FullPaperReviewRequest

ReviewBody = TypeAdapter(AbstractReviewRequest | FullPaperReviewRequest)


def test_accept_field_resolves_to_abstract_request():
    parsed = ReviewBody.validate_python({"accept": True, "notes": "ok"})
    assert isinstance(parsed, AbstractReviewRequest)
    assert parsed.accept is True


def test_decision_field_resolves_to_full_paper_request():
    parsed = ReviewBody.validate_python(
        {"decision": "revision", "notes": "fix section 3"}
    )
    assert isinstance(parsed, FullPaperReviewRequest)
    assert parsed.decision == "revision"


def test_body_matching_neither_shape_is_rejected():
    with pytest.raises(ValidationError):
        ReviewBody.validate_python({"nonsense": 1})


def test_empty_body_is_rejected():
    with pytest.raises(ValidationError):
        ReviewBody.validate_python({})


def test_openapi_publishes_a_request_schema_for_review():
    """Regression guard: the endpoint used to declare `body: dict`, which made
    OpenAPI publish no schema at all."""
    schema = app.openapi()
    path = schema["paths"]["/v1/api/articles/{id_article}/review"]["post"]
    body_schema = path["requestBody"]["content"]["application/json"]["schema"]
    rendered = str(body_schema)
    assert "AbstractReviewRequest" in rendered
    assert "FullPaperReviewRequest" in rendered
