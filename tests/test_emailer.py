"""The From header is the one part of a notification a recipient always sees,
and a malformed one gets the message refused or filed as spam — worth pinning
even though the send itself is only exercised against a live server.
"""

from email.message import EmailMessage

from src import email_template, emailer

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


# ---- message structure ----


def _message():
    return emailer.build_message(
        "author@example.com",
        "Abstract accepted",
        "Your abstract has been accepted.\n\nSubmit the full paper before 25 September 2026.",
    )


def test_both_a_text_and_an_html_part_are_sent():
    """HTML alone costs deliverability and leaves text-only readers nothing."""
    msg = _message()
    types = {p.get_content_type() for p in msg.walk()}
    assert "text/plain" in types
    assert "text/html" in types


def test_plain_text_is_exactly_what_the_caller_passed():
    """The text part is the caller's wording, not a stripped-down HTML render."""
    msg = _message()
    text = msg.get_body(preferencelist=("plain",)).get_content()
    assert "Your abstract has been accepted." in text
    assert "25 September 2026" in text
    assert "<" not in text


def test_logo_travels_with_the_message_as_an_inline_image():
    """A cid: reference survives a client that blocks remote images, and does
    not depend on the storage bucket staying publicly readable."""
    msg = _message()
    images = [p for p in msg.walk() if p.get_content_maintype() == "image"]
    assert len(images) == 1
    assert images[0].get("Content-ID") == f"<{email_template.LOGO_CID}>"
    html_body = msg.get_body(preferencelist=("html",)).get_content()
    assert f"cid:{email_template.LOGO_CID}" in html_body


def test_html_carries_no_remote_images():
    """Remote images make a message look like tracking and often get stripped."""
    html_body = _message().get_body(preferencelist=("html",)).get_content()
    assert "src=\"http" not in html_body
    assert "background-image" not in html_body


def test_body_text_reaches_the_html_part():
    html_body = _message().get_body(preferencelist=("html",)).get_content()
    assert "Your abstract has been accepted." in html_body
    assert "Submit the full paper" in html_body


def test_blank_lines_become_separate_paragraphs():
    html_body = _message().get_body(preferencelist=("html",)).get_content()
    assert html_body.count("<p style=\"margin:0 0 16px") == 2


def test_html_escapes_the_body_and_subject():
    """A paper title containing < or & must not break the markup."""
    msg = emailer.build_message("a@example.com", "Review of <Paper> & co", "See <b>this</b> & that")
    html_body = msg.get_body(preferencelist=("html",)).get_content()
    assert "&lt;b&gt;this&lt;/b&gt;" in html_body
    assert "<b>this</b>" not in html_body
    assert "&lt;Paper&gt;" in html_body


def test_layout_avoids_what_mail_clients_break():
    """Outlook renders through Word: no flex, no grid, no stylesheet."""
    html_body = _message().get_body(preferencelist=("html",)).get_content()
    for banned in ("display:flex", "display:grid", "<style", "<link"):
        assert banned not in html_body


def test_footer_names_the_event_and_the_organiser():
    html_body = _message().get_body(preferencelist=("html",)).get_content()
    assert email_template.ORGANISER in html_body
    assert "Ankara" in html_body


def test_renders_without_the_logo_when_the_file_is_missing():
    html_body = email_template.render("Subject", "Body", with_logo=False)
    assert "cid:" not in html_body
    assert "SIMIT" in html_body
