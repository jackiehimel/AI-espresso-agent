#!/usr/bin/env python3
"""
preview_edition.py — generate (optional), render, and open today's edition in a browser.

Usage:
    cd agent
    python3 preview_edition.py 2026-05-18
    python3 preview_edition.py 2026-05-18 --use-cache          # faster re-runs
    python3 preview_edition.py 2026-05-18 --render-only        # skip agent; use existing JSON
    python3 preview_edition.py 2026-05-18 --no-images          # text-only (faster)
    python3 preview_edition.py 2026-05-18 --no-open            # print URL only
    python3 preview_edition.py 2026-05-18 --write-archive      # opt in to archive mutation

Serves from editions/ so card images resolve (edition_N/assets/...).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from load_env import load_env_file
from render_html import DATA_EDITIONS_DIR, EDITIONS_DIR, render_edition

AGENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = AGENT_DIR.parent
DEFAULT_PORT = 8765


def _print_picks(edition_json: Path) -> None:
    data = json.loads(edition_json.read_text())
    print("\n── Picks (from JSON) ──", file=sys.stderr)
    for s in data.get("stories") or []:
        print(
            f"  [{s.get('slot', '?')}] {s.get('headline', '')}\n"
            f"       source: {s.get('source_name', '')}",
            file=sys.stderr,
        )
    for note in data.get("notes") or []:
        print(f"  note: {note}", file=sys.stderr)
    print(file=sys.stderr)


def _run_agent(date: dt.date, use_cache: bool, write_archive: bool) -> None:
    import espresso_agent

    print(f"\n[preview] running agent for {date} (use_cache={use_cache})…", file=sys.stderr)
    old_skip_archive = os.environ.get("ESPRESSO_SKIP_ARCHIVE")
    try:
        if not write_archive:
            os.environ["ESPRESSO_SKIP_ARCHIVE"] = "1"
        espresso_agent.run(date, dry_run=False, use_cache=use_cache, mode="agent")
    finally:
        if old_skip_archive is None:
            os.environ.pop("ESPRESSO_SKIP_ARCHIVE", None)
        else:
            os.environ["ESPRESSO_SKIP_ARCHIVE"] = old_skip_archive


def _run_render(date: str, no_images: bool) -> dict:
    cmd = [sys.executable, str(AGENT_DIR / "render_edition.py"), date]
    if no_images:
        cmd.append("--no-images")
    print(f"\n[preview] rendering: {' '.join(cmd)}", file=sys.stderr)
    out = subprocess.run(cmd, cwd=str(AGENT_DIR), capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)
        print(out.stdout, file=sys.stderr)
        raise SystemExit(out.returncode)
    if out.stderr:
        print(out.stderr, file=sys.stderr, end="")
    return json.loads(out.stdout)


def _serve_and_open(html_path: Path, port: int, open_browser: bool) -> None:
    # HTML lives at editions/edition_N_variant_c.html; img src is edition_N/assets/...
    serve_root = EDITIONS_DIR.resolve()
    rel = html_path.resolve().relative_to(serve_root)
    url = f"http://127.0.0.1:{port}/{rel.as_posix()}"

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(serve_root), **kwargs)

    httpd = None
    for attempt in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", attempt), Handler)
            port = attempt
            break
        except OSError as e:
            if e.errno != 48:  # Address already in use
                raise
    if httpd is None:
        raise SystemExit(f"no free port in {port}-{port + 9}")

    print(f"\n[preview] serving {serve_root}", file=sys.stderr)
    print(f"[preview] open: {url}", file=sys.stderr)
    print("[preview] Ctrl+C to stop the server\n", file=sys.stderr)

    if open_browser:
        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[preview] stopped.", file=sys.stderr)
        httpd.shutdown()


def main() -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description="Generate, render, and preview an edition in the browser")
    parser.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument("--render-only", action="store_true", help="Skip agent; use existing JSON")
    parser.add_argument("--use-cache", action="store_true", help="Pass use_cache=True to agent fetch")
    parser.add_argument(
        "--write-archive",
        action="store_true",
        help="Allow preview runs to append/upsert archive memory",
    )
    parser.add_argument("--no-images", action="store_true", help="Skip illustration generation")
    parser.add_argument("--no-open", action="store_true", help="Print URL only; do not open browser")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local server port (default {DEFAULT_PORT})")
    args = parser.parse_args()

    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    edition_json = DATA_EDITIONS_DIR / f"{date.isoformat()}.json"

    if not args.render_only:
        _run_agent(date, use_cache=args.use_cache, write_archive=args.write_archive)
    elif not edition_json.exists():
        print(f"error: no {edition_json}; run without --render-only first", file=sys.stderr)
        return 1

    if not edition_json.exists():
        print(f"error: agent did not write {edition_json}", file=sys.stderr)
        return 1

    _print_picks(edition_json)
    result = _run_render(date.isoformat(), no_images=args.no_images)
    html_path = Path(result["html_path"])
    if not html_path.is_file():
        print(f"error: missing {html_path}", file=sys.stderr)
        return 1

    print(f"\n[preview] HTML: {html_path}", file=sys.stderr)
    print(f"[preview] MD:   {result.get('md_path')}", file=sys.stderr)
    _serve_and_open(html_path, args.port, open_browser=not args.no_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
