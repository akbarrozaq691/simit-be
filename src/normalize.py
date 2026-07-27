"""Input normalisation for user-supplied text.

Participants type inconsistently — "budi santoso", "BUDI SANTOSO",
"  Universitas   Indonesia  ". These helpers settle on one representation so
listings, sorting and duplicate detection behave predictably.

Kept pure and dependency-free so the rules are unit-testable and identical
wherever they are applied (self-registration and admin-created accounts).
"""

import re

# Particles that stay lowercase inside a name unless they lead it. Indonesian
# and Turkish naming both use these; capitalising them reads wrong.
_LOWERCASE_PARTICLES = {
    "bin",
    "binti",
    "van",
    "von",
    "der",
    "den",
    "de",
    "da",
    "di",
    "dos",
    "del",
    "la",
    "le",
    "al",
    "and",
    "of",
    "the",
}

# Fragments that are acronyms, not words — keep them fully uppercase.
_KEEP_UPPER = {
    "ppi",
    "upi",
    "ui",
    "itb",
    "ugm",
    "its",
    "ipb",
    "uin",
    "iain",
    "stem",
    "simit",
    "phd",
    "msc",
    "bsc",
}

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def _capitalise_fragment(fragment: str, *, is_first: bool) -> str:
    lowered = fragment.lower()

    if lowered in _KEEP_UPPER:
        return fragment.upper()
    if not is_first and lowered in _LOWERCASE_PARTICLES:
        return lowered
    # Preserve a deliberate internal capital (McDonald, AlFatih) rather than
    # flattening it — but only when the fragment is genuinely mixed case.
    # SHOUTED input is the thing we are here to fix, so it gets flattened.
    has_lower = any(c.islower() for c in fragment)
    if len(fragment) > 1 and has_lower and any(c.isupper() for c in fragment[1:]):
        return fragment[0].upper() + fragment[1:]
    return lowered.capitalize()


def title_case(value: str | None) -> str | None:
    """Title-cases a name or institution, collapsing runs of whitespace.

    Returns None for None, and None for a value that is only whitespace —
    a blank string is absence of data, not data.
    """
    if value is None:
        return None
    collapsed = " ".join(value.split())
    if not collapsed:
        return None

    words = []
    for index, word in enumerate(collapsed.split(" ")):
        # Hyphenated and slashed parts capitalise independently: "sri-mulyani",
        # "fakultas teknik/informatika".
        for separator in ("-", "/"):
            if separator in word:
                word = separator.join(
                    _capitalise_fragment(part, is_first=(index == 0 and i == 0))
                    for i, part in enumerate(word.split(separator))
                )
                break
        else:
            word = _capitalise_fragment(word, is_first=(index == 0))
        words.append(word)
    return " ".join(words)


def normalize_email(value: str) -> str:
    """Lowercases and trims an address.

    The local part is technically case-sensitive per RFC 5321, but no mail
    provider in practice treats it that way, and `users.email` is UNIQUE —
    letting "Budi@x.com" and "budi@x.com" coexist as separate accounts would
    be a worse outcome than the theoretical incorrectness.
    """
    return value.strip().lower()


def normalize_phone(value: str | None) -> str | None:
    """Strips formatting characters, keeping a leading +.

    Returns None for None or a blank value. Does NOT validate — see
    `is_valid_phone`, which callers use to reject bad input with a clear error.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    plus = "+" if stripped.startswith("+") else ""
    digits = re.sub(r"\D", "", stripped)
    return f"{plus}{digits}" if digits else None


def is_valid_phone(value: str | None) -> bool:
    """True when the value is in E.164 form (`+` then 8-15 digits).

    None passes: the phone number is optional. The frontend does the
    country-aware validation with libphonenumber-js; this is the server-side
    floor that stops obviously malformed data from being stored.
    """
    if value is None:
        return True
    return bool(_E164.match(value))
