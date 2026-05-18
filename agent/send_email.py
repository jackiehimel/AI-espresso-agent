"""
send_email.py — deliver a rendered edition as a daily HTML email.

Sends the variant_c HTML edition as a multipart/alternative message with
inline images (CID attachments) so the four illustrations show up in
Outlook / Gmail / Apple Mail without needing public hosting.

Transport: SMTP via smtp.gmail.com:587 with STARTTLS. The sender must
have a Gmail App Password (https://myaccount.google.com/apppasswords),
not their normal account password — Google blocks plain-password SMTP.
DKIM is signed by Gmail's outbound infrastructure, so Microsoft 365
inboxes (like solvd.com) accept the mail cleanly without extra setup.

Config — set these as environment variables:
    AI_ESPRESSO_FROM        e.g. "himeljackie@gmail.com"
    AI_ESPRESSO_TO          comma-separated list, e.g. "jhimel@solvd.com"
    AI_ESPRESSO_FROM_NAME   optional display name, e.g. "AI Espresso"
    GMAIL_APP_PASSWORD      16-char app password from Google account
    AI_ESPRESSO_DRY_RUN     "1" to print instead of send (for cron testing)

Failures here never block the cron — render_edition still produces the
HTML/MD on disk; email is a delivery sidecar.
"""

from __future__ import annotations

import os
import re
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _rewrite_html_for_inline_images(html: str, asset_dir: Path) -> tuple[str, list[tuple[str, Path]]]:
    """
    Rewrite <img src="edition_N/assets/variant_c_NN.png"> tags so they point
    at cid:<msgid> references, and return the list of (cid, path) pairs the
    mailer needs to attach.

    We match any src that ends in `assets/variant_c_*.png` to stay robust
    against slightly different relative path prefixes ('./', '../', etc.).
    """
    attachments: list[tuple[str, Path]] = []
    pattern = re.compile(r'(<img[^>]+src=")([^"]*assets/(variant_c_\d+\.png))(")', re.IGNORECASE)

    def repl(match: re.Match) -> str:
        before, _, filename, after = match.groups()
        path = asset_dir / filename
        if not path.exists():
            # Leave the original src — the email will render with a broken
            # image, but the rest of the edition still goes through.
            print(f"  [warn] missing image {path}", file=sys.stderr)
            return match.group(0)
        cid = make_msgid(domain="ai-espresso.local")[1:-1]  # strip <>
        attachments.append((cid, path))
        return f"{before}cid:{cid}{after}"

    return pattern.sub(repl, html), attachments


def _plain_text_from_md(md: str) -> str:
    """Cheap MD → plain text. Good enough for the fallback part."""
    out = md
    out = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", out)        # drop images
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", out)  # flatten links
    out = re.sub(r"^#{1,6}\s+", "", out, flags=re.MULTILINE)   # strip headers
    out = re.sub(r"\*\*([^*]+)\*\*", r"\1", out)               # strip bold
    out = re.sub(r"\n{3,}", "\n\n", out)                        # collapse blanks
    return out.strip()


def send_edition_email(
    html_path: Path | str,
    md_path: Path | str | None = None,
    subject: str | None = None,
) -> dict:
    """
    Send one rendered edition as a daily email.

    Returns a status dict:
        {"sent": True, "to": [...]} on success
        {"sent": False, "reason": "..."} on any failure (config or SMTP)
    """
    html_path = Path(html_path)
    md_path = Path(md_path) if md_path else None

    if not html_path.exists():
        return {"sent": False, "reason": f"html not found: {html_path}"}

    sender = os.environ.get("AI_ESPRESSO_FROM")
    recipients_raw = os.environ.get("AI_ESPRESSO_TO", "")
    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    password = os.environ.get("GMAIL_APP_PASSWORD")
    sender_name = os.environ.get("AI_ESPRESSO_FROM_NAME", "AI Espresso")
    dry_run = os.environ.get("AI_ESPRESSO_DRY_RUN") == "1"

    if not sender or not recipients:
        return {
            "sent": False,
            "reason": "AI_ESPRESSO_FROM and AI_ESPRESSO_TO must be set",
        }
    if not password and not dry_run:
        return {
            "sent": False,
            "reason": "GMAIL_APP_PASSWORD must be set (or AI_ESPRESSO_DRY_RUN=1)",
        }

    html = html_path.read_text(encoding="utf-8")
    md = md_path.read_text(encoding="utf-8") if md_path and md_path.exists() else ""

    # Resolve the asset directory the HTML's <img src=> tags point into.
    # The renderer writes assets to `editions/edition_N/assets/*.png` and the
    # HTML lives at `editions/edition_N_variant_c.html`, so assets are at
    # `<html_parent>/<stem-minus-variant_c>/assets`.
    edition_stem = html_path.stem.replace("_variant_c", "")
    asset_dir = html_path.parent / edition_stem / "assets"

    html_rewritten, inline = _rewrite_html_for_inline_images(html, asset_dir)
    plain = _plain_text_from_md(md) if md else "Open in an HTML-capable mail client to view today's edition."

    if not subject:
        # Try to pull "No. NNN" or date from the HTML; otherwise fall back.
        no_match = re.search(r"NO\.\s*(\d+)", html)
        subject = f"AI Espresso · No. {no_match.group(1)}" if no_match else "AI Espresso"

    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(plain)
    msg.add_alternative(html_rewritten, subtype="html")

    # Attach images as inline (CID) parts on the HTML alternative.
    html_part = msg.get_payload()[1]
    for cid, path in inline:
        data = path.read_bytes()
        # All renderer output is PNG.
        html_part.add_related(data, maintype="image", subtype="png", cid=f"<{cid}>")

    if dry_run:
        print(f"[dry-run] would send '{subject}' to {recipients} with {len(inline)} inline images",
              file=sys.stderr)
        return {"sent": True, "to": recipients, "dry_run": True}

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(sender, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        return {"sent": False, "reason": f"smtp: {e}"}

    return {"sent": True, "to": recipients, "subject": subject, "inline_images": len(inline)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: send_email.py <edition.html> [edition.md] [subject]")
        sys.exit(1)
    html = sys.argv[1]
    md = sys.argv[2] if len(sys.argv) > 2 else None
    subj = sys.argv[3] if len(sys.argv) > 3 else None
    result = send_edition_email(html, md, subj)
    print(result)
    sys.exit(0 if result.get("sent") else 1)
