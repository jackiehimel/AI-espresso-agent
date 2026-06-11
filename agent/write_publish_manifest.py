#!/usr/bin/env python3
"""
Write a stable publish manifest for downstream consumers.

This captures the latest rendered edition artifact URLs in a machine-readable
contract so downstream systems can fetch only required files (no repo clone).
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _extract_relative_assets(html: str) -> list[str]:
    pattern = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    rel: list[str] = []
    seen: set[str] = set()
    for match in pattern.finditer(html):
        raw = match.group(1).strip()
        if (
            not raw
            or raw.startswith(("#", "/", "//"))
            or "://" in raw
            or raw.startswith(("data:", "mailto:", "tel:"))
        ):
            continue
        clean = raw.split("#", 1)[0].split("?", 1)[0]
        if not clean or ".." in clean.split("/"):
            continue
        if clean not in seen:
            seen.add(clean)
            rel.append(clean)
    return rel


def _must_be_relative_to_repo(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        p = p.resolve().relative_to(REPO_ROOT.resolve())
    return p


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--issue-num", type=int, required=True)
    parser.add_argument("--edition-date", required=True)
    parser.add_argument("--html-path", required=True)
    parser.add_argument("--md-path", required=True)
    parser.add_argument("--source-repo", required=True)
    parser.add_argument("--source-ref", default="main")
    parser.add_argument("--variant", default="c")
    args = parser.parse_args()

    html_rel = _must_be_relative_to_repo(args.html_path)
    md_rel = _must_be_relative_to_repo(args.md_path)
    html_abs = REPO_ROOT / html_rel
    md_abs = REPO_ROOT / md_rel
    if not html_abs.exists() or not md_abs.exists():
        raise FileNotFoundError("Rendered HTML/MD paths do not exist")

    html_text = html_abs.read_text(encoding="utf-8")
    asset_rel = _extract_relative_assets(html_text)
    artifact_asset_paths = [str((html_rel.parent / rel).as_posix()) for rel in asset_rel]

    raw_base = f"https://raw.githubusercontent.com/{args.source_repo}/{args.source_ref}"
    manifest = {
        "schema_version": 1,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "source_repo": args.source_repo,
        "source_ref": args.source_ref,
        "edition": {
            "number": args.issue_num,
            "base": f"edition_{args.issue_num}_variant_{args.variant}",
            "variant": args.variant,
            "date": args.edition_date,
        },
        "artifacts": {
            "html": {
                "path": str(html_rel.as_posix()),
                "url": f"{raw_base}/{html_rel.as_posix()}",
            },
            "markdown": {
                "path": str(md_rel.as_posix()),
                "url": f"{raw_base}/{md_rel.as_posix()}",
            },
            "assets": [
                {"path": p, "url": f"{raw_base}/{p}"}
                for p in artifact_asset_paths
            ],
        },
    }

    out = REPO_ROOT / "editions" / "publish" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(str(out))


if __name__ == "__main__":
    main()
