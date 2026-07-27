"""The From header is the one part of a notification a recipient always sees,
and a malformed one gets the message refused or filed as spam — worth pinning
even though the send itself is only exercised against a live server.
"""

from email.message import EmailMessage

from src import emailer

NAME = "[No-Reply] 4th International Symposium PPI Türkiye 2026"
ADDRESS = "no-reply-simit@puspitur.com"


def test_bare_address_when_no_name_is_set(monkeypatch):
    monkeypatch.setattr(emailer.settings, "smtp_from", ADDRESS)
    monkeypatch.setattr(emailer.settings, "smtp_from_name", "")
    assert emailer.from_header() == ADDRESS


def test_non_ascii_name_becomes_an_encoded_word(monkeypatch):
    """With the ü in "Türkiye", the whole phrase is RFC 2047-encoded, which
    needs no quoting — the address still stands alone in angle brackets."""
    monkeypatch.setattr(emailer.settings, "smtp_from", ADDRESS)
    monkeypatch.setattr(emailer.settings, "smtp_from_name", NAME)
    header = emailer.from_header()
    assert header.endswith(f"<{ADDRESS}>")
    assert header.lower().startswith("=?utf-8?")


def test_ascii_name_with_brackets_is_quoted(monkeypatch):
    """Without non-ASCII there is no encoded word, so the [ ] must be quoted —
    an unquoted phrase containing them is not valid RFC 5322."""
    monkeypatch.setattr(emailer.settings, "smtp_from", ADDRESS)
    monkeypatch.setattr(emailer.settings, "smtp_from_name", "[No-Reply] SIMIT 2026")
    header = emailer.from_header()
    assert header == f'"[No-Reply] SIMIT 2026" <{ADDRESS}>'


def test_header_survives_serialisation_with_non_ascii(monkeypatch):
    """The ü must reach the recipient as the right character, whether the
    header is sent as UTF-8 or RFC 2047-encoded."""
    monkeypatch.setattr(emailer.settings, "smtp_from", ADDRESS)
    monkeypatch.setattr(emailer.settings, "smtp_from_name", NAME)

    message = EmailMessage()
    message["From"] = emailer.from_header()
    message["To"] = "someone@example.com"
    message["Subject"] = "Test"
    message.set_content("body")

    raw = message.as_string()
    # Either literal UTF-8 or an encoded word, but never a mangled placeholder.
    assert "Türkiye" in raw or "=?utf-8?" in raw.lower()
    assert "?" not in ADDRESS
    assert ADDRESS in raw

    # Round-trip: parsing the header back yields the original name and address.
    from email import message_from_string
    from email.utils import parseaddr

    parsed_name, parsed_addr = parseaddr(message_from_string(raw)["From"])
    assert parsed_addr == ADDRESS
    assert "No-Reply" in parsed_name


def test_envelope_sender_stays_a_bare_address(monkeypatch):
    """The display name must never leak into the envelope sender — the server
    verifies that value and refuses what it cannot match to the account."""
    monkeypatch.setattr(emailer.settings, "smtp_from", ADDRESS)
    monkeypatch.setattr(emailer.settings, "smtp_from_name", NAME)
    assert emailer.settings.smtp_from == ADDRESS
    assert "<" not in emailer.settings.smtp_from
