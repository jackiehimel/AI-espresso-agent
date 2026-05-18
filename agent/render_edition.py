"""
render_edition.py — end-to-end edition rendering.

Usage:
    python3 render_edition.py 2026-05-21
    python3 render_edition.py 2026-05-21 --no-images
    python3 render_edition.py 2026-05-21 --allow-missing-images

Pipeline:
  1. Load data/editions/<DATE>.json
  2. Render HTML + MD to ../editions/edition_N_variant_c.{html,md}
  3. Generate the 4 illustrations to ../editions/edition_N/assets/

This is the entry point the daily cron calls after the agent has produced
its JSON output.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from load_env import load_env_file
from render_html import DATA_EDITIONS_DIR, EDITIONS_DIR, render_edition
from render_images import render_images


def exit_code_for_images(
    image_result: dict,
    *,
    allow_missing: bool,
    image_paths: list[Path] | None = None,
) -> int:
    """Return 1 when required illustrations are missing or too small (CI gate)."""
    if allow_missing:
        return 0
    if image_result.get("missing"):
        return 1
    if image_paths:
        for raw in image_paths:
            path = Path(raw)
            if not path.is_file() or path.stat().st_size <= 10_000:
                return 1
    return 0


def main() -> int:
    load_env_file()
    parser = argparse.ArgumentParser()
    parser.add_argument("date", help="Edition date in YYYY-MM-DD")
    parser.add_argument("--issue-num", type=int, default=None,
                        help="Override the auto-assigned issue number")
    parser.add_argument("--no-images", action="store_true",
                        help="Skip image generation (text-only sample)")
    parser.add_argument("--images-only", action="store_true",
                        help="Regenerate PNGs only (HTML must already exist)")
    parser.add_argument("--allow-missing-images", action="store_true",
                        help="Exit 0 even when illustration PNGs failed to generate")
    args = parser.parse_args()

    # Validate the date and find the JSON
    try:
        dt.date.fromisoformat(args.date)
    except ValueError:
        print(f"error: bad date {args.date!r} (expected YYYY-MM-DD)", file=sys.stderr)
        return 2

    edition_json = DATA_EDITIONS_DIR / f"{args.date}.json"
    if not edition_json.exists():
        print(f"error: no edition JSON at {edition_json}", file=sys.stderr)
        return 1

    print(f"[render] reading {edition_json}", file=sys.stderr)
    edition_data = json.loads(edition_json.read_text())

    if args.images_only:
        issue_num = args.issue_num
        if issue_num is None:
            issue_num = edition_data.get("issue_num") or 2
        from render_html import image_filename

        result = {
            "issue_num": issue_num,
            "html_path": str(EDITIONS_DIR / f"edition_{issue_num}_variant_c.html"),
            "md_path": str(EDITIONS_DIR / f"edition_{issue_num}_variant_c.md"),
            "image_paths": [
                EDITIONS_DIR / image_filename(issue_num, i) for i in range(1, 5)
            ],
        }
        print("[render] --images-only: skipping HTML", file=sys.stderr)
    else:
        result = render_edition(edition_json, issue_num=args.issue_num)
        edition_data = json.loads(edition_json.read_text())
        print(f"[render] wrote {result['html_path']}", file=sys.stderr)
        print(f"[render] wrote {result['md_path']}", file=sys.stderr)

    if args.no_images:
        print("[render] skipping image generation (--no-images)", file=sys.stderr)
        payload = {
            "issue_num": result["issue_num"],
            "html_path": result["html_path"],
            "md_path": result["md_path"],
            "images": {"generated": [], "missing": []},
        }
        print(json.dumps(payload, indent=2))
        return exit_code_for_images(
            payload["images"],
            allow_missing=args.allow_missing_images,
            image_paths=result.get("image_paths"),
        )

    print("[render] generating illustrations...", file=sys.stderr)
    image_result = render_images(result, edition_data)
    print(f"[render] images: {len(image_result['generated'])} ok, "
          f"{len(image_result['missing'])} missing", file=sys.stderr)

    # HTML is written before images; refresh so <img> tags match generated PNGs.
    print("[render] refreshing HTML with image paths...", file=sys.stderr)
    result = render_edition(edition_json, issue_num=result["issue_num"])
    print(f"[render] wrote {result['html_path']}", file=sys.stderr)
    print(f"[render] wrote {result['md_path']}", file=sys.stderr)

    payload = {
        "issue_num": result["issue_num"],
        "html_path": result["html_path"],
        "md_path": result["md_path"],
        "images": image_result,
    }
    print(json.dumps(payload, indent=2))
    code = exit_code_for_images(image_result, allow_missing=args.allow_missing_images)
    if code != 0:
        missing = image_result.get("missing") or []
        print(
            f"error: {len(missing)} illustration(s) missing (use --allow-missing-images to override)",
            file=sys.stderr,
        )
        for path in missing:
            print(f"  missing: {path}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
