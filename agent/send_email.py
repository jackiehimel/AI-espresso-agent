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
    AI_ESPRESSO_EXPECTED_DATE  optional YYYY-MM-DD guard against stale sends

Failures here never block the cron — render_edition still produces the
HTML/MD on disk; email is a delivery sidecar.
"""

from __future__ import annotations

import os
import json
import re
import smtplib
import sys
import datetime as dt
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from pathlib import Path

from bs4 import BeautifulSoup


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_OUTER_MAX_WIDTH = 680
EMAIL_CARD_MAX_WIDTH = 310
EMAIL_IMAGE_SIZE = 160
EMAIL_GRID_COLS = 2
CATEGORY_COLORS = {
    "market": "#00955A",
    "everyday": "#D14A0E",
    "build": "#C13B0E",
    "industry": "#5C6B8A",
    "news": "#8B6F47",
}


def _node_text(node, default: str = "") -> str:
    return node.get_text(" ", strip=True) if node else default

def _stabilize_inline_img_tags(html: str) -> str:
    """Give CID images explicit dimensions for Outlook."""
    pattern = re.compile(
        r'<img([^>]*?)src="(cid:[^"]+)"([^>]*)>',
        re.IGNORECASE,
    )

    def repl(match: re.Match) -> str:
        before, src, after = match.groups()
        # Explicit width/height attributes are more reliable than CSS-only sizing in Outlook.
        return (
            f'<img{before}src="{src}"{after} width="{EMAIL_IMAGE_SIZE}" height="{EMAIL_IMAGE_SIZE}" '
            f'style="display:block;width:{EMAIL_IMAGE_SIZE}px;height:{EMAIL_IMAGE_SIZE}px;max-width:{EMAIL_IMAGE_SIZE}px;'
            'border:0;outline:none;text-decoration:none;margin:12px auto 0 auto;">'
        )

    return pattern.sub(repl, html)


def _build_email_safe_html(html: str) -> str:
    """Convert web layout HTML into a robust email layout."""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one(".container") or soup

    wordmark = container.select_one(".wordmark")
    tagline = container.select_one(".tagline")
    dateline = container.select_one(".dateline")
    footer = container.select_one(".footer")

    story_cells = []
    for card in container.select(".story-cards .card"):
        img = card.select_one(".card-image")
        category = card.select_one(".category")
        headline_link = card.select_one(".headline-link")
        kicker = card.select_one(".kicker")
        source = card.select_one(".source")
        source_link = source.select_one("a") if source else None
        source_tail = ""
        if source:
            source_tail = source.get_text(" ", strip=True)
            if source_link:
                source_name_text = source_link.get_text(" ", strip=True)
                source_tail = source_tail.removeprefix(source_name_text).strip()

        cat_cls = ""
        if category:
            classes = category.get("class", [])
            cat_cls = next((c for c in classes if c in CATEGORY_COLORS), "")
        cat_color = CATEGORY_COLORS.get(cat_cls, "#8B6F47")

        img_src = img.get("src", "") if img else ""
        img_alt = img.get("alt", "") if img else ""
        headline_href = headline_link.get("href", "#") if headline_link else "#"
        headline_text = headline_link.get_text(" ", strip=True) if headline_link else ""
        category_text = _node_text(category)
        kicker_html = kicker.decode_contents() if kicker else ""
        source_name = _node_text(source_link)
        source_href = source_link.get("href", "#") if source_link else "#"

        story_cells.append(
            f"<td align=\"center\" valign=\"top\" width=\"{100 // EMAIL_GRID_COLS}%\" style=\"padding:0 6px 18px 6px;\">"
            f"<table role=\"presentation\" width=\"{EMAIL_CARD_MAX_WIDTH}\" cellpadding=\"0\" cellspacing=\"0\" "
            f"style=\"width:100%;max-width:{EMAIL_CARD_MAX_WIDTH}px;background:#FFFFFF;border:1px solid #EFE5D6;border-radius:12px;\">"
            "<tr><td align=\"center\" style=\"padding:12px 12px 0 12px;\">"
            f"<img src=\"{escape(img_src, quote=True)}\" alt=\"{escape(img_alt, quote=True)}\" "
            f"width=\"{EMAIL_IMAGE_SIZE}\" height=\"{EMAIL_IMAGE_SIZE}\" "
            f"style=\"display:block;width:{EMAIL_IMAGE_SIZE}px;height:{EMAIL_IMAGE_SIZE}px;border:0;outline:none;text-decoration:none;\">"
            "</td></tr>"
            "<tr><td style=\"padding:10px 12px 12px 12px;\">"
            f"<p style=\"margin:0 0 4px 0;font-size:10px;font-weight:800;letter-spacing:0.18em;text-transform:uppercase;color:{cat_color};\">{escape(category_text)}</p>"
            f"<h2 style=\"margin:0 0 4px 0;font-size:14px;line-height:1.25;font-weight:700;color:#1A1108;\">"
            f"<a href=\"{escape(headline_href, quote=True)}\" style=\"color:#1A1108;text-decoration:none;\">{escape(headline_text)}</a></h2>"
            f"<p style=\"margin:0 0 6px 0;font-size:12px;line-height:1.35;font-style:italic;color:#1A1108;\">{kicker_html}</p>"
            "<p style=\"margin:0;border-top:1px solid #F0E5D1;padding-top:6px;font-size:11px;color:#8B6F47;\">"
            f"<a href=\"{escape(source_href, quote=True)}\" style=\"color:#1A1108;text-decoration:none;font-weight:700;\">{escape(source_name)}</a>"
            f"{(' ' + escape(source_tail)) if source_tail else ''}</p>"
            "</td></tr></table></td>"
        )

    story_rows = []
    for i in range(0, len(story_cells), EMAIL_GRID_COLS):
        chunk = story_cells[i:i + EMAIL_GRID_COLS]
        if len(chunk) < EMAIL_GRID_COLS:
            chunk += [f"<td width=\"{100 // EMAIL_GRID_COLS}%\"></td>"] * (EMAIL_GRID_COLS - len(chunk))
        story_rows.append("<tr>" + "".join(chunk) + "</tr>")

    prompt_row = ""
    prompt_card = container.select_one(".prompt-card")
    if prompt_card:
        prompt_tag = prompt_card.select_one(".prompt-tag")
        prompt_title = prompt_card.select_one(".prompt-title")
        prompt_hint = prompt_card.select_one(".prompt-tool-hint")
        prompt_code = prompt_card.select_one(".prompt-code")
        prompt_max = EMAIL_OUTER_MAX_WIDTH - 32
        prompt_row = (
            f"<tr><td align=\"center\" colspan=\"{EMAIL_GRID_COLS}\" style=\"padding:0 6px 18px 6px;\">"
            f"<table role=\"presentation\" width=\"{prompt_max}\" cellpadding=\"0\" cellspacing=\"0\" "
            f"style=\"width:100%;max-width:{prompt_max}px;background:#FFF8E8;border:1px dashed #C9A671;border-radius:12px;\">"
            "<tr><td style=\"padding:14px;\">"
            f"<p style=\"margin:0 0 12px 0;font-size:13px;font-weight:800;letter-spacing:0.18em;text-transform:uppercase;color:#B8340A;"
            f"background:#F2C9B8;padding:8px 12px;border-radius:6px;text-align:center;\">{escape(_node_text(prompt_tag))}</p>"
            f"<p style=\"margin:0 0 10px 0;font-size:14px;line-height:1.25;font-weight:800;color:#1A1108;\">{escape(_node_text(prompt_title))}</p>"
            f"<p style=\"margin:0 0 10px 0;font-size:14px;line-height:1.35;font-style:italic;color:#5C4A3A;\">{escape(_node_text(prompt_hint))}</p>"
            f"<div style=\"background:#FFFFFF;border:1px solid #E8DCC8;border-radius:6px;padding:10px 12px;font-family:Consolas,Menlo,monospace;"
            f"font-size:11px;line-height:1.5;color:#1A1108;white-space:pre-wrap;\">{escape(_node_text(prompt_code))}</div>"
            "</td></tr></table></td></tr>"
        )

    footer_html = footer.decode_contents() if footer else ""
    wordmark_html = wordmark.decode_contents() if wordmark else "ai espresso"
    tagline_text = _node_text(tagline, "your morning cup of AI")
    dateline_text = _node_text(dateline)

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "</head><body style=\"margin:0;padding:0;background:#F4EFE6;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1A1108;\">"
        "<table role=\"presentation\" width=\"100%\" cellpadding=\"0\" cellspacing=\"0\" style=\"background:#F4EFE6;\">"
        "<tr><td align=\"center\" style=\"padding:24px 16px;\">"
        f"<table role=\"presentation\" width=\"{EMAIL_OUTER_MAX_WIDTH}\" cellpadding=\"0\" cellspacing=\"0\" style=\"width:100%;max-width:{EMAIL_OUTER_MAX_WIDTH}px;\">"
        f"<tr><td colspan=\"{EMAIL_GRID_COLS}\" style=\"text-align:center;padding:8px 16px 10px 16px;\">"
        f"<h1 style=\"margin:0;font-size:44px;line-height:1;font-weight:800;color:#1A1108;\">{wordmark_html}</h1>"
        f"<p style=\"margin:6px 0 0 0;font-size:20px;font-weight:550;color:#5C4A3A;\">{escape(tagline_text)}</p>"
        f"<p style=\"margin:8px 0 0 0;font-size:11px;font-weight:700;letter-spacing:0.22em;text-transform:uppercase;color:#8B6F47;\">{escape(dateline_text)}</p>"
        "</td></tr>"
        f"{''.join(story_rows)}"
        f"{prompt_row}"
        f"<tr><td colspan=\"{EMAIL_GRID_COLS}\" style=\"text-align:center;padding:28px 16px 8px 16px;font-size:11px;color:#8B6F47;line-height:1.7;letter-spacing:0.04em;\">"
        f"{footer_html}</td></tr>"
        "</table></td></tr></table></body></html>"
    )


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


def _issue_num_from_html_path(html_path: Path) -> int | None:
    m = re.search(r"edition_(\d+)_variant_[a-z]\.html$", html_path.name)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _edition_date_for_issue(repo_root: Path, issue_num: int) -> str | None:
    editions_dir = repo_root / "agent" / "data" / "editions"
    if not editions_dir.is_dir():
        return None
    matches: list[str] = []
    for path in editions_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("issue_num") == issue_num:
            date = payload.get("date")
            if isinstance(date, str):
                matches.append(date)
    if len(matches) != 1:
        return None
    return matches[0]


def _sent_log_path(repo_root: Path) -> Path:
    return repo_root / "agent" / "data" / "sent_editions.json"


def _load_sent_dates(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    rows = payload.get("sent_dates")
    if not isinstance(rows, list):
        return set()
    return {d for d in rows if isinstance(d, str)}


def _record_sent_date(path: Path, date_iso: str) -> None:
    sent = _load_sent_dates(path)
    sent.add(date_iso)
    payload = {
        "schema_version": 1,
        "sent_dates": sorted(sent),
        "updated_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _validate_expected_date_guard(html_path: Path, expected_date: str | None) -> str | None:
    """Reject sends when the rendered artifact does not map to the expected edition date."""
    if not expected_date:
        return None
    issue_num = _issue_num_from_html_path(html_path)
    if issue_num is None:
        return (
            "stale-send guard: expected date is set, but HTML filename is not "
            "edition_<N>_variant_<x>.html"
        )

    editions_dir = html_path.parent
    if editions_dir.name != "editions":
        return "stale-send guard: expected HTML under /editions"
    repo_root = editions_dir.parent
    edition_json = repo_root / "agent" / "data" / "editions" / f"{expected_date}.json"
    if not edition_json.exists():
        return (
            f"stale-send guard: expected edition JSON missing for {expected_date} "
            f"({edition_json})"
        )
    try:
        payload = json.loads(edition_json.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"stale-send guard: failed to parse expected edition JSON: {exc}"
    expected_issue = payload.get("issue_num")
    if not isinstance(expected_issue, int):
        return (
            f"stale-send guard: expected edition JSON for {expected_date} has no issue_num"
        )
    if expected_issue != issue_num:
        return (
            f"stale-send guard: expected {expected_date} issue {expected_issue}, "
            f"but HTML is issue {issue_num}"
        )
    return None


def _resolve_target_date(html_path: Path, expected_date: str | None) -> tuple[str | None, str | None]:
    """Return (date_iso, error)."""
    if expected_date:
        err = _validate_expected_date_guard(html_path, expected_date)
        if err:
            return None, err
        return expected_date, None

    issue_num = _issue_num_from_html_path(html_path)
    if issue_num is None:
        return None, (
            "no-resend guard: expected edition filename edition_<N>_variant_<x>.html"
        )
    if html_path.parent.name != "editions":
        return None, "no-resend guard: expected HTML under /editions"
    repo_root = html_path.parent.parent
    date_iso = _edition_date_for_issue(repo_root, issue_num)
    if not date_iso:
        return None, (
            f"no-resend guard: could not resolve edition date for issue {issue_num}; "
            "set AI_ESPRESSO_EXPECTED_DATE explicitly"
        )
    return date_iso, None


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
    expected_date = (os.environ.get("AI_ESPRESSO_EXPECTED_DATE") or "").strip()

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
    force_resend = os.environ.get("AI_ESPRESSO_FORCE_RESEND") == "1"
    target_date, date_error = _resolve_target_date(html_path, expected_date or None)
    if date_error:
        return {"sent": False, "reason": date_error}
    repo_root = html_path.parent.parent
    sent_path = _sent_log_path(repo_root)
    if not force_resend and target_date in _load_sent_dates(sent_path):
        return {
            "sent": False,
            "reason": f"no-resend policy: edition date {target_date} already sent",
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
    html_rewritten = _stabilize_inline_img_tags(html_rewritten)
    html_rewritten = _build_email_safe_html(html_rewritten)
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

    _record_sent_date(sent_path, target_date)
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
