"""
render_html.py — render a JSON edition into the variant_c HTML + Markdown
shape expected by the AI Garage portal sync.

Outputs two files at the repo root's editions/ dir:
    editions/edition_N_variant_c.html
    editions/edition_N_variant_c.md
where N is the next monotonic issue number.

The HTML template is a 1:1 copy of the variant_c reference shipped in
ai-garage/editions/latest.html, with story content swapped in. The
manifest sniffer in sync-espresso.mjs looks for:
    NO.&nbsp;NNN          (label)
    DAY DD.MM.YY         (date)
    N&nbsp;SHOTS          (shot count)
    <span style="display:none">HEADLINE</span>  (preheader)
All four are embedded.

Image paths reference edition_N/assets/variant_c_NN.png relative to the
edition HTML. render_images.py is responsible for producing those PNGs.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from card_config import MAX_STORY_COUNT, MIN_STORY_COUNT, STORY_CARD_COUNT
from editorial import slot_label


# ---------- paths ----------
REPO_ROOT = Path(__file__).resolve().parent.parent
EDITIONS_DIR = REPO_ROOT / "editions"
DATA_EDITIONS_DIR = Path(__file__).resolve().parent / "data" / "editions"

# Public footer (Solvd internal edition — not personal email or stale repo name).
FOOTER_CONTACT_EMAIL = "jhimel@solvd.com"
FOOTER_REPO_URL = "https://github.com/jackiehimel/AI-espresso-agent"
FOOTER_CONTACT_SUBJECT = "AI%20Espresso%20issue%20report"
EDITION_TAGLINE = "your morning cup of AI"


def edition_footer_html() -> str:
    mailto = (
        f"mailto:{FOOTER_CONTACT_EMAIL}?subject={FOOTER_CONTACT_SUBJECT}"
    )
    return (
        f'brewed by ai espresso · <a href="{mailto}">spot something off?</a> · '
        f'<a href="{FOOTER_REPO_URL}">repo</a>'
    )


def edition_footer_md() -> str:
    mailto = (
        f"mailto:{FOOTER_CONTACT_EMAIL}?subject={FOOTER_CONTACT_SUBJECT}"
    )
    return (
        f"*brewed by ai espresso · [spot something off?]({mailto}) · "
        f"[repo]({FOOTER_REPO_URL})*"
    )


# ---------- issue numbering ----------
def next_issue_number(editions_dir: Path = EDITIONS_DIR) -> int:
    """Find the highest existing edition_N and return N + 1. Start at 1."""
    editions_dir.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(r"^edition_(\d+)(?:_variant_[a-z0-9]+)?\.(?:html|md)$")
    nums = []
    for f in editions_dir.iterdir():
        m = pattern.match(f.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def resolve_issue_num(
    issue_num: int | None,
    data: dict[str, Any],
    edition_json_path: Path | None = None,
    editions_dir: Path = EDITIONS_DIR,
) -> int:
    """Assign a stable, per-edition issue number.

    Numbers are keyed to the edition itself, not to whatever artifacts happen to
    sit in ``editions_dir``. The first render of an edition claims the next free
    number and persists it back into the edition JSON; later renders of the same
    edition reuse that number, so re-runs are idempotent and a new date never
    overwrites a previously shipped edition.
    """
    if issue_num is not None:
        return issue_num
    persisted = data.get("issue_num")
    if isinstance(persisted, int) and persisted > 0:
        return persisted
    assigned = next_issue_number(editions_dir)
    if edition_json_path is not None:
        data["issue_num"] = assigned
        edition_json_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
    return assigned


# ---------- date formatting ----------
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def format_dateline(date_iso: str) -> dict[str, str]:
    """Return all the date strings the template + sniffer need."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    day_abbr = DAYS[d.weekday()]
    month_abbr = MONTHS[d.month - 1]
    return {
        "iso": date_iso,
        "dateline_html": f"{day_abbr.title()} &middot; {month_abbr.title()} {d.day} &middot; {d.year}",
        "dateline_md": f"{day_abbr} · {month_abbr} {d.day} · {d.year}",
        # Match visible dateline (archive/manifest); avoid DD.MM.YY (reads as "May 26").
        "sniffer_date": (
            f"{day_abbr}&nbsp;&middot;&nbsp;{month_abbr}&nbsp;{d.day}"
            f"&nbsp;&middot;&nbsp;{d.year}"
        ),
        "source_short": f"{month_abbr.title()} {d.day}",
    }


# ---------- kicker derivation ----------
def derive_kicker(story: dict[str, Any]) -> str:
    """
    The variant_c template uses a short italic 'kicker' line under the headline
    (e.g. "$5B/year — months after Musk was publicly trashing them."). Our
    agent's JSON has `blurb` (the body) and `why_it_matters` (the angle). We
    prefer why_it_matters as the kicker; fall back to the first sentence of
    blurb if missing.
    """
    why = (story.get("why_it_matters") or "").strip()
    if why:
        # Bold the first numeric-looking phrase if present, like the template
        # does. Best-effort: match $5B, 180 production sites, +15.2%, etc.
        m = re.search(r"(\$?\d[\d,.]*\s*(?:%|[A-Za-z][\w/+\-]*)?)", why)
        if m and len(m.group(0)) >= 3:
            span = m.group(0)
            why = why.replace(span, f"<strong>{escape(span)}</strong>", 1)
        else:
            why = escape(why)
        return why
    blurb = (story.get("blurb") or "").strip()
    first = blurb.split(". ")[0].rstrip(".")
    return escape(first)


# ---------- image filename convention ----------
def image_filename(issue: int, idx: int) -> str:
    """edition_N/assets/variant_c_01.png style path."""
    return f"edition_{issue}/assets/variant_c_{idx:02d}.png"


def _card_image_block(
    issue_num: int,
    idx: int,
    alt: str,
    editions_dir: Path,
) -> str:
    """Image markup; cream placeholder block if the PNG is missing."""
    rel = image_filename(issue_num, idx)
    if not (editions_dir / rel).is_file():
        return (
            '      <div class="card-image card-image--placeholder" '
            'aria-hidden="true"></div>\n'
        )
    return f'      <img class="card-image" src="{rel}" alt="{escape(alt)}">\n'


# ---------- HTML template ----------
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Espresso · Edition {issue_num} · Variant C</title>
<!-- preheader (hidden, used by manifest sniffer) -->
<span style="display:none">{preheader}</span>
<!-- sniffer-friendly meta tokens -->
<meta name="ai-espresso-issue" content="NO.&nbsp;{issue_padded}">
<meta name="ai-espresso-date" content="{sniffer_date}">
<meta name="ai-espresso-shots" content="{shots_label}">
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 24px 16px;
    background-color: #F4EFE6;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    color: #1A1108;
    line-height: 1.5;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }}
  .container {{ max-width: 1100px; margin: 0 auto; }}

  .masthead {{ text-align: center; padding: 8px 16px 10px 16px; }}
  .wordmark {{
    font-size: 44px; font-weight: 800; letter-spacing: -0.035em;
    margin: 0; color: #1A1108; line-height: 1;
  }}
  .wordmark .cup {{ font-weight: 400; margin-left: 4px; }}
  .dateline {{
    font-size: 11px; font-weight: 700; color: #8B6F47;
    letter-spacing: 0.22em; text-transform: uppercase; margin: 8px 0 0 0;
  }}
  .tagline {{
    font-size: 20px;
    font-weight: 550;
    color: #5C4A3A;
    letter-spacing: 0.01em;
    margin: 6px 0 0 0;
  }}

  .edition-grid {{
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 20px;
    align-items: start;
    max-width: 1100px;
    margin: 0 auto;
  }}
  .story-cards {{
    grid-column: 1 / span 4;
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 20px;
    align-items: stretch;
  }}
  .story-cards .card {{
    height: 100%;
  }}
  .story-cards .card-body {{
    flex: 1 1 auto;
    min-height: 0;
  }}
  .story-cards .kicker {{
    flex: 1 1 auto;
  }}
  .story-cards .source {{
    margin-top: auto;
  }}
  .edition-grid .prompt-card {{
    grid-column: 5;
  }}
  .section-divider {{
    display: none;
    text-align: center; color: #C9A671; font-size: 13px;
    letter-spacing: 0.35em; margin: 10px 0; user-select: none;
  }}
  .card {{
    background-color: #FFFFFF; border-radius: 12px; overflow: hidden;
    border: none;
    box-shadow: 0 1px 2px rgba(28,17,10,0.04), 0 4px 12px rgba(28,17,10,0.05);
    display: flex; flex-direction: column;
    height: auto;
  }}
  .card-image {{
    width: 160px;
    height: 160px;
    max-width: calc(100% - 24px);
    aspect-ratio: 1 / 1;
    object-fit: cover;
    object-position: center;
    display: block;
    margin: 12px auto 0 auto;
    flex-shrink: 0;
  }}
  .card-image--placeholder {{
    width: 160px;
    height: 160px;
    max-width: calc(100% - 24px);
    aspect-ratio: 1 / 1;
    display: block;
    margin: 12px auto 0 auto;
    background-color: #F5F0E8;
    border-radius: 8px;
    flex-shrink: 0;
  }}
  .card-body {{
    padding: 12px 14px 14px 14px;
    display: flex; flex-direction: column; flex: 0 1 auto;
    justify-content: flex-start;
  }}
  .card-link {{
    display: block;
    text-decoration: none;
    color: inherit;
  }}
  .category {{
    font-size: 10px; font-weight: 800; letter-spacing: 0.18em;
    text-transform: uppercase; margin: 0 0 6px 0;
  }}
  .category.market {{ color: #00955A; }}
  .category.everyday {{ color: #D14A0E; }}
  .category.build {{ color: #C13B0E; }}
  .category.industry {{ color: #5C6B8A; }}
  .category.news {{ color: #8B6F47; }}
  .headline {{
    font-size: 15px; font-weight: 700; line-height: 1.25;
    margin: 0 0 6px 0; color: #1A1108; letter-spacing: -0.01em;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .headline-link {{
    color: #1A1108;
    text-decoration: none;
  }}
  .headline-link:hover {{
    text-decoration: underline;
  }}
  .kicker {{
    font-size: 13px; color: #1A1108; margin: 0 0 8px 0;
    font-style: italic; font-weight: 500; line-height: 1.4;
    flex: 0 0 auto;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .source {{
    font-size: 11px; color: #8B6F47; letter-spacing: 0.02em;
    margin: 0; border-top: 1px solid #F0E5D1; padding-top: 8px;
  }}
  .source a {{ color: #1A1108; text-decoration: none; font-weight: 700; }}

  .prompt-card {{
    position: relative;
    background: linear-gradient(165deg, #FFF8E8 0%, #FFF4D6 55%, #F9E9C8 100%);
    border: 1px dashed #C9A671;
    border-radius: 12px;
    box-shadow: 0 2px 8px rgba(193, 59, 14, 0.08);
    align-self: start;
  }}
  .prompt-card .card-image,
  .prompt-card .card-image--placeholder {{
    display: none;
  }}
  .prompt-card .card-body {{
    display: flex; flex-direction: column;
    justify-content: flex-start;
    padding: 14px 14px 14px 14px;
  }}
  .prompt-tag {{
    display: block;
    font-size: 13px; font-weight: 800; letter-spacing: 0.18em;
    color: #B8340A; text-transform: uppercase; margin: 0 0 12px 0;
    background-color: #F2C9B8; padding: 8px 42px 8px 12px; border-radius: 6px;
    text-align: center;
  }}
  .prompt-title {{
    font-size: 14px; font-weight: 800; line-height: 1.25;
    margin: 0 0 10px 0; color: #1A1108; letter-spacing: -0.01em;
  }}
  .prompt-tool-hint {{
    font-size: 14px; font-style: italic; color: #5C4A3A;
    margin: 0 0 10px 0; line-height: 1.4;
  }}
  .prompt-code-wrap {{
    flex: 0 0 auto;
  }}
  .prompt-copy {{
    position: absolute;
    top: 18px;
    right: 22px;
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    padding: 0;
    border: 1px solid #E8DCC8;
    border-radius: 6px;
    background-color: #FFFFFF;
    color: #6D4C41;
    cursor: pointer;
  }}
  .prompt-copy:hover {{
    color: #1A1108;
    border-color: #C9A671;
    background-color: #FFFDF8;
  }}
  .prompt-copy:focus-visible {{
    outline: 2px solid #D14A0E;
    outline-offset: 2px;
  }}
  .prompt-copy--done {{
    color: #00955A;
    border-color: #00955A;
  }}
  .prompt-code {{
    background-color: #FFFFFF; border: 1px solid #E8DCC8; border-radius: 6px;
    padding: 10px 12px;
    font-family: "SF Mono", "Menlo", "Monaco", "Consolas", monospace;
    font-size: 11px; color: #1A1108; line-height: 1.5; white-space: pre-wrap;
  }}

  .qotd {{
    margin: 28px auto 0 auto; max-width: 520px; text-align: center;
  }}
  .qotd-label {{
    font-size: 10px; font-weight: 800; letter-spacing: 0.22em;
    color: #D14A0E; text-transform: uppercase; margin: 0 0 10px 0;
  }}
  .qotd-question {{
    font-size: 15px; font-weight: 600; color: #1A1108; margin: 0 0 12px 0;
    line-height: 1.4;
  }}
  .qotd-form {{ display: flex; gap: 8px; justify-content: center; flex-wrap: wrap; }}
  .qotd-input {{
    flex: 1 1 220px; max-width: 320px; padding: 10px 14px; border-radius: 10px;
    border: 1px solid #E8DCC8; font-size: 13px; font-family: inherit;
    background-color: #FFFFFF;
  }}
  .qotd-submit {{
    padding: 10px 18px; border-radius: 10px; border: none;
    background-color: #D14A0E; color: #FFFFFF; font-weight: 700;
    font-size: 12px; cursor: pointer; letter-spacing: 0.04em;
  }}
  .qotd-submit:disabled {{ opacity: 0.6; cursor: default; }}
  .qotd-done {{ font-size: 12px; color: #00955A; margin: 10px 0 0 0; display: none; }}
  .qotd-error {{ font-size: 12px; color: #B8340A; margin: 10px 0 0 0; display: none; }}
  .qotd-preview {{
    font-size: 12px; color: #8B6F47; margin: 0; line-height: 1.5;
  }}

  .footer {{
    text-align: center; padding: 28px 16px 8px 16px;
    font-size: 11px; color: #8B6F47; line-height: 1.7; letter-spacing: 0.04em;
  }}
  .footer a {{ color: #8B6F47; text-decoration: underline; }}

  @media (max-width: 1279px) and (min-width: 960px) {{
    .edition-grid {{ grid-template-columns: 1fr; }}
    .story-cards {{
      grid-column: 1;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }}
    .edition-grid .prompt-card {{ grid-column: 1; }}
    .section-divider {{ display: block; grid-column: 1 / -1; }}
  }}
  @media (max-width: 959px) and (min-width: 768px) {{
    .edition-grid {{ grid-template-columns: 1fr; gap: 14px; }}
    .story-cards {{
      grid-column: 1;
      grid-template-columns: 1fr;
    }}
    .edition-grid .prompt-card {{ grid-column: 1; }}
    .section-divider {{ display: none; }}
  }}
  @media (max-width: 767px) {{
    .edition-grid {{ grid-template-columns: 1fr; gap: 14px; }}
    .story-cards {{
      grid-column: 1;
      grid-template-columns: 1fr;
    }}
    .edition-grid .prompt-card {{ grid-column: 1; }}
    .section-divider {{ display: none; }}
  }}
  @media (max-width: 520px) {{
    .wordmark {{ font-size: 36px; }}
    body {{ padding: 18px 12px; }}
    .card-body {{ padding: 16px 18px 18px 18px; }}
    .headline {{ font-size: 19px; }}
    .kicker {{ font-size: 14.5px; }}
    .prompt-code {{ font-size: 12px; }}
  }}
</style>
</head>
<body>
<div class="container">

  <header class="masthead">
    <h1 class="wordmark">ai espresso<span class="cup">&nbsp;☕</span></h1>
    <p class="tagline">{tagline_html}</p>
    <p class="dateline">{dateline_html}</p>
  </header>

  <section class="edition-grid">
{edition_cards}
  </section>

{qotd_section}

  <footer class="footer">
    {footer_html}
  </footer>

</div>
<script>
{qotd_script}
(function () {{
  var btn = document.querySelector(".prompt-copy");
  var code = document.querySelector(".prompt-code");
  if (!btn || !code) return;
  function copyPrompt() {{
    var text = code.textContent || "";
    function onSuccess() {{
      btn.classList.add("prompt-copy--done");
      btn.setAttribute("aria-label", "Copied");
      btn.setAttribute("title", "Copied");
      setTimeout(function () {{
        btn.classList.remove("prompt-copy--done");
        btn.setAttribute("aria-label", "Copy prompt to clipboard");
        btn.setAttribute("title", "Copy to clipboard");
      }}, 2000);
    }}
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(onSuccess).catch(fallback);
      return;
    }}
    fallback();
    function fallback() {{
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      try {{
        if (document.execCommand("copy")) onSuccess();
      }} finally {{
        document.body.removeChild(ta);
      }}
    }}
  }}
  btn.addEventListener("click", copyPrompt);
}})();
</script>
</body>
</html>
"""

STORY_CARD_TEMPLATE = """    <article class="card">
{image_block}      <div class="card-body">
        <p class="category {cat_cls}">{cat_label}</p>
        <h2 class="headline"><a class="headline-link" href="{source_url}">{headline}</a></h2>
        <p class="kicker">{kicker}</p>
        <p class="source"><a class="source-link" href="{source_url}">{source_name}</a> · {source_date}</p>
      </div>
    </article>"""

PROMPT_CARD_TEMPLATE = """    <article class="card prompt-card">
{image_block}      <button type="button" class="prompt-copy" aria-label="Copy prompt to clipboard" title="Copy to clipboard">
        {copy_icon}
      </button>
      <div class="card-body">
        <p class="prompt-tag">☕ Try this prompt</p>
{prompt_title_block}{prompt_tool_hint_block}        <div class="prompt-code-wrap">
          <div class="prompt-code">{prompt_body}</div>
        </div>
      </div>
    </article>"""

PROMPT_COPY_ICON = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true">'
    '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
    '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
    "</svg>"
)


# ---------- Markdown template ----------
MD_TEMPLATE = """# ai espresso ☕ — Edition {issue_num} · Variant C (Newspaper Comic · Snackable)

*{tagline_md}*
**{dateline_md}**

---

{md_stories}
---

![{prompt_alt}]({prompt_image})

**☕ Try this prompt**

{prompt_title_md}{prompt_tool_hint_md}
```
{prompt_body}
```

---

{footer_md}
"""

MD_STORY_TEMPLATE = """![{alt}]({image})

**{cat_upper}**

## {headline}

{blurb}

*{why_plain}*

[{source_name}]({source_url}) · {source_date}

---

"""


# ---------- manifest preheader (hidden only) ----------
PREHEADER_MAX_CHARS = 140


def derive_preheader(stories: list[dict[str, Any]]) -> str:
    """Hidden sniffer text from story headlines — not shown on the page."""
    headlines = [
        (s.get("headline") or "").strip().rstrip(".")
        for s in stories[:STORY_CARD_COUNT]
        if (s.get("headline") or "").strip()
    ]
    if not headlines:
        return "AI Espresso daily edition"
    joined = " · ".join(headlines)
    if len(joined) <= PREHEADER_MAX_CHARS:
        return joined
    return headlines[0][:PREHEADER_MAX_CHARS].rstrip()


def _prompt_title_block(title: str) -> str:
    title = (title or "").strip()
    if not title:
        return ""
    return f'        <h2 class="prompt-title">{escape(title)}</h2>\n'


def _prompt_tool_hint_block(tool_hint: str) -> str:
    hint = (tool_hint or "").strip()
    if not hint:
        return ""
    return f'        <p class="prompt-tool-hint">{escape(hint)}</p>\n'


def qotd_api_base() -> str | None:
    """Base URL for QOTD API (no trailing slash). Unset = static/preview edition."""
    raw = (os.environ.get("AI_ESPRESSO_QOTD_API_URL") or "").strip()
    if not raw:
        return None
    return raw.rstrip("/")


def build_qotd_section(daily_question: str, api_base: str | None) -> str:
    """QOTD block: interactive form when hosted, honest preview copy otherwise."""
    label = (
        '  <section class="qotd" id="qotd">\n'
        '    <p class="qotd-label">Question of the day</p>\n'
        f'    <p class="qotd-question">{daily_question}</p>\n'
    )
    if api_base:
        return (
            label
            + '    <form class="qotd-form" id="qotd-form">\n'
            + '      <input class="qotd-input" type="text" name="answer" maxlength="280"\n'
            + '        placeholder="Your one-sentence take&hellip;" required aria-label="Your answer">\n'
            + '      <button class="qotd-submit" type="submit">Submit</button>\n'
            + "    </form>\n"
            + '    <p class="qotd-done" id="qotd-done">Thanks — recorded.</p>\n'
            + '    <p class="qotd-error" id="qotd-error"></p>\n'
            + "  </section>"
        )
    return (
        label
        + '    <p class="qotd-preview">Preview edition — answers are collected when this issue is hosted online.</p>\n'
        + "  </section>"
    )


def build_qotd_script(date_json: str, api_base: str | None) -> str:
    """Client script for QOTD submit; empty when no API base (static editions)."""
    if not api_base:
        return ""
    api_url_json = json.dumps(f"{api_base}/api/daily-question")
    return f"""(function () {{
  var form = document.getElementById("qotd-form");
  var done = document.getElementById("qotd-done");
  var err = document.getElementById("qotd-error");
  if (!form) return;
  form.addEventListener("submit", function (e) {{
    e.preventDefault();
    var input = form.querySelector("input[name=answer]");
    var btn = form.querySelector("button[type=submit]");
    if (err) {{ err.style.display = "none"; err.textContent = ""; }}
    btn.disabled = true;
    fetch({api_url_json}, {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{ date: {date_json}, answer: (input && input.value) || "" }})
    }}).then(function (res) {{
      if (!res.ok) throw new Error("request failed");
      if (done) done.style.display = "block";
      if (input) input.disabled = true;
    }}).catch(function () {{
      if (err) {{
        err.textContent = "Could not save your answer. Please try again later.";
        err.style.display = "block";
      }}
      btn.disabled = false;
    }});
  }});
}})();
"""


# ---------- main render ----------
def render_edition(
    edition_json_path: Path,
    issue_num: int | None = None,
    editions_dir: Path = EDITIONS_DIR,
) -> dict[str, Any]:
    """
    Render the given edition JSON into edition_N_variant_c.{html,md} under
    editions_dir. Returns a dict with the issue number and output paths.
    """
    data = json.loads(edition_json_path.read_text())
    stories = data.get("stories", [])
    if len(stories) < MIN_STORY_COUNT:
        raise ValueError(
            f"Edition has {len(stories)} stories; need at least {MIN_STORY_COUNT}."
        )
    prompt = data.get("try_this_prompt") or {}
    story_limit = min(len(stories), MAX_STORY_COUNT)

    issue_num = resolve_issue_num(issue_num, data, edition_json_path, editions_dir)
    issue_padded = f"{issue_num:03d}"
    dates = format_dateline(data["date"])
    preheader = derive_preheader(stories)
    daily_question = escape(
        (data.get("daily_question") or "What's one AI tool you'd actually use at work this week?").strip()
    )
    date_json = json.dumps(data["date"])
    api_base = qotd_api_base()
    qotd_section = build_qotd_section(daily_question, api_base)
    qotd_script = build_qotd_script(date_json, api_base)
    shots_label = f"{story_limit + 1}&nbsp;SHOTS"

    # ---- story cards ----
    html_cards = []
    md_stories = []
    for idx, s in enumerate(stories[:story_limit], start=1):
        cat_label, cat_cls = slot_label(s.get("slot", ""))
        img = image_filename(issue_num, idx)
        headline = s.get("headline", "").strip()
        alt = headline[:120]
        kicker = derive_kicker(s)
        source_name = s.get("source_name") or s.get("source", "Source")
        source_url = s.get("source_url") or s.get("url", "#")
        source_url_html = escape(source_url, quote=True)
        image_markup = _card_image_block(issue_num, idx, alt, editions_dir).rstrip("\n")
        image_block = (
            f'      <a class="card-link" href="{source_url_html}">\n'
            f"{image_markup}\n"
            "      </a>\n"
        )
        html_cards.append(STORY_CARD_TEMPLATE.format(
            image_block=image_block,
            cat_label=cat_label,
            cat_cls=cat_cls,
            headline=escape(headline),
            kicker=kicker,  # already escaped + may contain <strong>
            source_url=source_url_html,
            source_name=escape(source_name),
            source_date=dates["source_short"],
        ))
        md_stories.append(MD_STORY_TEMPLATE.format(
            alt=alt,
            image=img,
            cat_upper=cat_label,
            headline=headline,
            blurb=(s.get("blurb") or "").strip(),
            why_plain=(s.get("why_it_matters") or "").strip(),
            source_name=source_name,
            source_url=source_url,
            source_date=dates["source_short"],
        ))

    prompt_idx = story_limit + 1
    prompt_img = image_filename(issue_num, prompt_idx)
    prompt_body = (prompt.get("prompt") or "").strip()
    prompt_tip_raw = (prompt.get("tool_hint") or "").strip()
    prompt_title = (prompt.get("title") or "").strip()
    prompt_alt = (prompt_title or "AI Espresso prompt card")[:120]
    prompt_card = PROMPT_CARD_TEMPLATE.format(
        image_block=_card_image_block(issue_num, prompt_idx, prompt_alt, editions_dir),
        prompt_title_block=_prompt_title_block(prompt_title),
        prompt_tool_hint_block=_prompt_tool_hint_block(prompt_tip_raw),
        prompt_body=escape(prompt_body),
        copy_icon=PROMPT_COPY_ICON,
    )
    story_cards_row = (
        '    <div class="story-cards">\n'
        + "\n".join(html_cards[:story_limit])
        + "\n    </div>"
    )
    edition_cards = story_cards_row + "\n" + prompt_card

    html = HTML_TEMPLATE.format(
        issue_num=issue_num,
        issue_padded=issue_padded,
        preheader=escape(preheader),
        sniffer_date=dates["sniffer_date"],
        shots_label=shots_label,
        dateline_html=dates["dateline_html"],
        tagline_html=escape(EDITION_TAGLINE),
        edition_cards=edition_cards,
        qotd_section=qotd_section,
        qotd_script=qotd_script,
        footer_html=edition_footer_html(),
    )

    prompt_title_md = f"### {prompt_title}\n\n" if prompt_title else ""
    prompt_tool_hint_md = f"*{prompt_tip_raw}*\n\n" if prompt_tip_raw else ""
    md = MD_TEMPLATE.format(
        issue_num=issue_num,
        dateline_md=dates["dateline_md"],
        tagline_md=EDITION_TAGLINE,
        prompt_title_md=prompt_title_md,
        prompt_tool_hint_md=prompt_tool_hint_md,
        md_stories="".join(md_stories),
        prompt_alt="Try this prompt",
        prompt_image=prompt_img,
        prompt_body=prompt_body,
        footer_md=edition_footer_md(),
    )

    editions_dir.mkdir(parents=True, exist_ok=True)
    html_path = editions_dir / f"edition_{issue_num}_variant_c.html"
    md_path = editions_dir / f"edition_{issue_num}_variant_c.md"
    html_path.write_text(html)
    md_path.write_text(md)

    return {
        "issue_num": issue_num,
        "html_path": str(html_path),
        "md_path": str(md_path),
        "image_paths": [
            editions_dir / image_filename(issue_num, i) for i in range(1, prompt_idx + 1)
        ],
        "stories": stories[:story_limit],
        "prompt": {"body": prompt_body, "tool_hint": prompt_tip_raw},
        "preheader": preheader,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: render_html.py <edition_json_path> [issue_num]")
        sys.exit(1)
    result = render_edition(
        Path(sys.argv[1]),
        issue_num=int(sys.argv[2]) if len(sys.argv) > 2 else None,
    )
    print(json.dumps({
        "issue_num": result["issue_num"],
        "html_path": result["html_path"],
        "md_path": result["md_path"],
        "image_paths_expected": [str(p) for p in result["image_paths"]],
    }, indent=2))
