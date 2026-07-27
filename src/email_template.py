"""The HTML shell around every notification email.

Written the way email demands rather than the way a web page would be: tables
for layout, styles inline, a fixed 600px content column. Grid, flexbox and
stylesheets are unreliable across mail clients — Outlook in particular renders
through Word — so none are used here.

The logo travels with the message as an inline attachment (a `cid:` reference)
instead of a hosted URL. That keeps the email intact when a client blocks remote
images, and it means the footer does not depend on the storage bucket staying
public.
"""

import html
import pathlib

LOGO_PATH = pathlib.Path(__file__).with_name("email_assets") / "simit-logo.jpg"
LOGO_CID = "simitlogo"

EVENT_NAME = "4th International Student Symposium in Türkiye"
ORGANISER = "Pusat Studi PPI Türkiye"

# Brand plum and orange, as solid values: gradients are ignored or mangled by
# several clients, so each surface gets one dependable colour.
PLUM = "#922B67"
PLUM_DARK = "#5F1841"
INK = "#3A3330"
MUTED = "#6B625C"
PAGE_BG = "#F4EFEC"
# The logo artwork sits on pure black, so the footer band is pure black too and
# the JPEG blends in seamlessly — no transparency, and nothing for a mail client
# to get wrong. Anything near-black instead leaves the logo showing as a visible
# rectangle.
FOOTER_BG = "#000000"

# Web-safe stack: mail clients have no access to webfonts, and leaving this
# unset gets the whole message rendered in the client's serif default.
FONT = "Arial, 'Helvetica Neue', Helvetica, sans-serif"


def _paragraphs(body: str) -> str:
    """Turns the plain-text body into paragraphs, escaping it on the way.

    Callers pass text written for the text/plain part; escaping here is what
    keeps a title containing `<` or `&` from breaking the markup.
    """
    blocks = [b.strip() for b in body.split("\n\n") if b.strip()]
    out = []
    for block in blocks:
        # A single newline inside a block is a line break, not a new paragraph.
        text = "<br>".join(html.escape(line) for line in block.splitlines())
        out.append(
            f'<p style="margin:0 0 16px;font-family:{FONT};font-size:15px;line-height:1.6;color:{INK};">{text}</p>'
        )
    return "\n".join(out)


def render(subject: str, body: str, *, with_logo: bool = True) -> str:
    """The full HTML message for one notification."""
    logo_cell = (
        f'<img src="cid:{LOGO_CID}" alt="{html.escape(EVENT_NAME)}" width="220" '
        f'style="display:block;border:0;outline:none;text-decoration:none;width:220px;max-width:220px;height:auto;">'
        if with_logo
        else f'<span style="font-family:{FONT};font-size:18px;font-weight:bold;color:#ffffff;">SIMIT</span>'
    )

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background-color:{PAGE_BG};font-family:{FONT};">
<!-- Preheader: the snippet a client shows beside the subject. -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{html.escape(subject)}</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background-color:{PAGE_BG};">
  <tr>
    <td align="center" style="padding:28px 12px;">

      <!-- The width attribute is for Outlook, which ignores max-width; the
           percentage is what lets the card shrink on a phone instead of forcing
           a sideways scroll. -->
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
             style="width:100%;max-width:600px;background-color:#ffffff;border-radius:12px;overflow:hidden;">
        <tr>
          <td style="height:4px;background-color:{PLUM};font-size:0;line-height:0;">&nbsp;</td>
        </tr>

        <tr>
          <td style="padding:28px 32px 8px;">
            <p style="margin:0 0 4px;font-family:{FONT};font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:{PLUM};font-weight:bold;">
              SIMIT 2026
            </p>
            <h1 style="margin:0 0 18px;font-family:{FONT};font-size:20px;line-height:1.35;color:{PLUM_DARK};font-weight:bold;">
              {html.escape(subject)}
            </h1>
          </td>
        </tr>

        <tr>
          <td style="padding:0 32px 26px;">
            {_paragraphs(body)}
            <p style="margin:22px 0 0;font-family:{FONT};font-size:13px;line-height:1.6;color:{MUTED};">
              This is an automated message about your submission. Sign in to the
              SIMIT portal to see the full status of your paper.
            </p>
          </td>
        </tr>

        <tr>
          <td style="background-color:{FOOTER_BG};padding:22px 32px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td align="left" valign="middle" style="padding:0 0 14px;">
                  {logo_cell}
                </td>
              </tr>
              <tr>
                <td align="left" valign="top">
                  <p style="margin:0 0 4px;font-family:{FONT};font-size:13px;line-height:1.5;color:#ffffff;font-weight:bold;">
                    {html.escape(EVENT_NAME)}
                  </p>
                  <p style="margin:0 0 10px;font-family:{FONT};font-size:12px;line-height:1.5;color:#B9AEB4;">
                    Organised by {html.escape(ORGANISER)} &middot; Ankara, T&uuml;rkiye
                  </p>
                  <p style="margin:0;font-family:{FONT};font-size:11px;line-height:1.5;color:#8A7F85;">
                    You received this because you have an account on the SIMIT
                    submission portal. Replies to this address are not monitored.
                  </p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>

      <p style="margin:14px 0 0;font-family:{FONT};font-size:11px;color:{MUTED};">
        &copy; 2026 {html.escape(ORGANISER)}
      </p>

    </td>
  </tr>
</table>
</body>
</html>
"""
