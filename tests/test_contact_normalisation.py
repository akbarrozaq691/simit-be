"""The NormalisedContact mixin, which every account-carrying request shares.

Phone handling had a hole worth pinning: text with no digits normalises to None,
and None is legitimate because the field is optional, so "not a phone" was
accepted and stored as nothing while "123" was properly refused.
"""

import pytest
from pydantic import ValidationError

from src.schemas import UserUpdate


def test_valid_international_number_is_kept():
    assert UserUpdate(phone_number="+90 555 123 4567").phone_number == "+905551234567"


def test_formatting_characters_are_stripped():
    assert UserUpdate(phone_number="+62 (812) 3456-7890").phone_number == "+6281234567890"


def test_omitted_phone_stays_none():
    assert UserUpdate().phone_number is None


def test_blank_phone_is_treated_as_omitted():
    assert UserUpdate(phone_number="   ").phone_number is None


@pytest.mark.parametrize("bad", ["not a phone", "-", "n/a", "call me"])
def test_text_without_digits_is_refused_rather_than_dropped(bad):
    with pytest.raises(ValidationError) as exc:
        UserUpdate(phone_number=bad)
    assert "country code" in str(exc.value)


@pytest.mark.parametrize("bad", ["123", "0812", "+1"])
def test_numbers_that_are_not_dialable_are_refused(bad):
    with pytest.raises(ValidationError):
        UserUpdate(phone_number=bad)


def test_name_is_title_cased_and_email_lowercased():
    u = UserUpdate(user_name="akbar rozaq", email="Akbar@Example.COM")
    assert u.user_name == "Akbar Rozaq"
    assert u.email == "akbar@example.com"


def test_an_edit_can_carry_role_and_email():
    """Both were absent from the schema, so an admin could rename an account but
    not fix its address or move it between roles."""
    u = UserUpdate(email="new@example.com", name_role="SC", occupation_name="Dosen")
    assert u.email == "new@example.com"
    assert u.name_role == "SC"
    assert u.occupation_name == "Dosen"


def test_institution_is_left_exactly_as_typed():
    """Deliberate: people know how their own organisation is written."""
    assert UserUpdate(institution_name="ITB").institution_name == "ITB"
    assert (
        UserUpdate(institution_name="universitas gadjah mada").institution_name
        == "universitas gadjah mada"
    )
