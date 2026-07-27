import pytest

from src.normalize import (
    collapse_whitespace,
    is_valid_phone,
    normalize_email,
    normalize_phone,
    title_case,
)


class TestTitleCase:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("budi santoso", "Budi Santoso"),
            ("BUDI SANTOSO", "Budi Santoso"),
            ("bUdI sAnToSo", "BUdI SAnToSo"),  # internal capitals are preserved
            ("  budi   santoso  ", "Budi Santoso"),
        ],
    )
    def test_basic_casing(self, raw, expected):
        assert title_case(raw) == expected

    def test_collapses_internal_whitespace(self):
        assert title_case("budi     santoso    junior") == "Budi Santoso Junior"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("muhammad bin abdullah", "Muhammad bin Abdullah"),
            ("siti binti hasan", "Siti binti Hasan"),
            ("jan van der berg", "Jan van der Berg"),
        ],
    )
    def test_particles_stay_lowercase_unless_leading(self, raw, expected):
        assert title_case(raw) == expected

    def test_leading_particle_is_still_capitalised(self):
        assert title_case("van houten") == "Van Houten"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("ppi turkiye", "PPI Turkiye"),
            ("muhammad phd", "Muhammad PHD"),
        ],
    )
    def test_known_acronyms_stay_uppercase(self, raw, expected):
        assert title_case(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("sri-mulyani indrawati", "Sri-Mulyani Indrawati"),
        ],
    )
    def test_hyphens_and_slashes_capitalise_each_part(self, raw, expected):
        assert title_case(raw) == expected

    def test_none_passes_through(self):
        assert title_case(None) is None

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_becomes_none(self, blank):
        """A whitespace-only value is absence of data, not data."""
        assert title_case(blank) is None


class TestCollapseWhitespace:
    """Institution and freely-typed occupation keep their capitalisation.

    Participants know how their own organisation and job title are written, so
    only whitespace is tidied — the one change that cannot lose information.
    """

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("LIPI", "LIPI"),
            ("ITB", "ITB"),
            ("Universitas Gadjah Mada", "Universitas Gadjah Mada"),
            ("universitas gadjah mada", "universitas gadjah mada"),
            ("PhD Candidate", "PhD Candidate"),
            ("R&D Engineer", "R&D Engineer"),
        ],
    )
    def test_capitalisation_is_left_alone(self, raw, expected):
        assert collapse_whitespace(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("  LIPI  ", "LIPI"),
            ("Institut    Teknologi   Bandung", "Institut Teknologi Bandung"),
        ],
    )
    def test_whitespace_is_tidied(self, raw, expected):
        assert collapse_whitespace(raw) == expected

    def test_none_passes_through(self):
        assert collapse_whitespace(None) is None

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_blank_becomes_none(self, blank):
        assert collapse_whitespace(blank) is None


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Budi@Simit.Local", "budi@simit.local"),
            ("  BUDI@SIMIT.LOCAL  ", "budi@simit.local"),
            ("budi@simit.local", "budi@simit.local"),
        ],
    )
    def test_lowercases_and_trims(self, raw, expected):
        assert normalize_email(raw) == expected


class TestNormalizePhone:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+90 555 123 4567", "+905551234567"),
            ("+90-555-123-4567", "+905551234567"),
            ("+90 (555) 123 4567", "+905551234567"),
            ("  +905551234567  ", "+905551234567"),
            ("08123456789", "08123456789"),  # no + means we keep it as typed digits
        ],
    )
    def test_strips_formatting(self, raw, expected):
        assert normalize_phone(raw) == expected

    def test_none_passes_through(self):
        assert normalize_phone(None) is None

    @pytest.mark.parametrize("blank", ["", "   ", "+", "-- --"])
    def test_blank_or_digitless_becomes_none(self, blank):
        assert normalize_phone(blank) is None


class TestIsValidPhone:
    @pytest.mark.parametrize(
        "value",
        ["+905551234567", "+6281234567890", "+12125551234", "+441234567890"],
    )
    def test_accepts_e164(self, value):
        assert is_valid_phone(value) is True

    def test_none_is_allowed_because_phone_is_optional(self):
        assert is_valid_phone(None) is True

    @pytest.mark.parametrize(
        "value",
        [
            "08123456789",  # no country code
            "+0123456789",  # country codes never start with 0
            "+123",  # too short
            "+1234567890123456",  # too long (E.164 caps at 15 digits)
            "not a phone",
            "+90 555 123 4567",  # must be normalised before validation
        ],
    )
    def test_rejects_malformed(self, value):
        assert is_valid_phone(value) is False
