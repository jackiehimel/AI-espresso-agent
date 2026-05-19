"""
render_images.py — generate the four illustrations a variant_c edition needs.

Each card gets a distinct composition, accent color, and visual metaphor (think
Christoph Niemann / Saul Steinberg editorial wit — not clipart still-lifes).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _normalize_prompt_for_cli(prompt: str) -> str:
    """Replace Unicode punctuation that breaks ASCII stderr/subprocess on some macOS shells."""
    for src, dst in (
        ("\u2018", "'"), ("\u2019", "'"),
        ("\u201c", '"'), ("\u201d", '"'),
        ("\u2014", " - "), ("\u2013", "-"),
        ("\u2026", "..."),
    ):
        prompt = prompt.replace(src, dst)
    return prompt


# Per-card art direction so the four tiles don't look like clones.
# card_index: 0–2 = stories (engineer, beginner, business order), 3 = prompt tile
# Vintage newspaper engraving — slate-blue ink on aged cream (original variant_c look).
ILLUSTRATION_STYLE = (
    "Vintage newspaper editorial engraving illustration, fine steel-blue charcoal ink "
    "with dense cross-hatching and stippling for shadows, warm aged-paper cream background, "
    "Wall Street Journal hedcut or 1960s New Yorker spot-drawing quality, confident linework, "
    "readable at card thumbnail size. NOT photorealistic, NOT flat cartoon clipart, "
    "NOT light pencil sketch, NOT washed-out watercolor, NOT pure black ink on white."
)

# Composition-only — mood must come from the story scene, not fixed card index
# (index-based finance/drama moods caused piggy-bank and clapperboard bleed).
CARD_PROFILES: list[dict[str, str]] = [
    {
        "composition": "Clear focal object, generous negative space, readable at thumbnail size.",
        "accent": "slate-blue charcoal ink on cream paper only",
        "mood": "literal and calm",
        "technique": "fine cross-hatching and stippling, engraving linework",
    },
    {
        "composition": "Centered still life, one hero object, minimal props.",
        "accent": "slate-blue charcoal ink on cream paper only",
        "mood": "literal and calm",
        "technique": "fine cross-hatching and stippling, engraving linework",
    },
    {
        "composition": "Wide frame, one hero object, room to breathe.",
        "accent": "slate-blue charcoal ink on cream paper only",
        "mood": "literal and calm",
        "technique": "fine cross-hatching and stippling, engraving linework",
    },
    {
        "composition": "Playful centered square, one bold metaphor, maximum breathing room.",
        "accent": "slate-blue charcoal ink on cream paper only",
        "mood": "light editorial humor",
        "technique": "fine cross-hatching, engraving linework, not faint",
    },
]


TEXT_SUPPRESSION = (
    "ABSOLUTELY CRITICAL: no text, no letters, no words, no numbers, "
    "no labels, no captions, no handwriting, no signage, no logos anywhere. "
    "Screens on devices must be blank dark rectangles."
)


def _style_envelope(profile: dict[str, str]) -> str:
    return (
        "Contemporary editorial illustration for a smart Sunday magazine — "
        "Christoph Niemann / Saul Steinberg sensibility: witty visual metaphor, "
        "dark bold hand-drawn ink on warm cream paper, NOT light pencil sketch, "
        "NOT washed out, NOT photorealistic, NOT generic clipart, NOT brown monotone "
        "still-life, NOT every object on a café table. "
        f"Mood: {profile['mood']}. Technique: {profile['technique']}. "
        f"Accent color: {profile['accent']}. "
        "No people, no faces, no hands, no robots, no brains, no circuit boards, "
        "no sci-fi chrome. Objects only."
    )


SCENE_SYSTEM = """You write a visual scene brief for a newspaper illustration.

The reader must understand the headline from the picture in 3 seconds.
Use a CLEAR, LITERAL still life — recognizable objects tied to the story topic.
A light visual twist is OK only if it stays obvious (e.g. cane beside an office chair).

Rules:
- 2-3 objects max. One focal subject.
- NO surreal mashups (no piggy banks inside TVs, no wires spilling from briefcases).
- NO café table, coffee cup, espresso, still-life trio, generic desk clutter.
- NO people, faces, or hands (objects only).
- Smartphones only when the story is about mobile — blank dark screen.
- No text, letters, numbers, or logos in the scene.
- Output one short paragraph, no lists."""


SCENE_SCHEMA = {
    "type": "object",
    "properties": {"scene": {"type": "string"}},
    "required": ["scene"],
}


def _is_social_coding_story(text: str) -> bool:
    """Coding agent inside a social feed — must run before generic 'interactive' video."""
    if any(k in text for k in ("grok", "grok build", "x posts", "social post", "in your x")):
        return True
    return "coding agent" in text and any(
        k in text for k in ("post", "social", "platform", "deploy", "browser")
    )


def _safe_social_coding_scene() -> str:
    """Brand-free still life for social-feed coding stories (avoids Gemini moderation triggers)."""
    return (
        "A smartphone with a blank dark screen showing a simple speech-bubble shape "
        "with tiny angle brackets inside it, beside a small upload-arrow icon on a "
        "plain surface — code shared inside a social post, no logos or brand marks."
    )


def _curated_scene(headline: str, blurb: str, slot: str, card_index: int) -> str:
    """Keyword-matched scene briefs from story text — not image file paths."""
    text = f"{headline} {blurb}".lower()
    if any(
        k in text
        for k in (
            "prediction market", "polymarket", "kalshi", "insider trading",
            "cftc", "betting pattern", "suspicious betting",
        )
    ):
        return (
            "A yes/no prediction slip beside a magnifying glass over a simple line "
            "chart with one sharp spike — regulators watching prediction-market trades."
        )
    if any(k in text for k in ("cerebras", "chip maker", "wafer", "ai chip")) or (
        "ipo" in text and any(k in text for k in ("stock", "debut", "public", "trading", "market"))
    ):
        return (
            "A large square computer chip on a small pedestal with a simple upward "
            "arrow chart beside it — AI chip company going public."
        )
    if any(k in text for k in ("older worker", "older workers", "hiring power", "hiring", "résumé", "resume")):
        return (
            "A wooden cane resting against an office chair, with a neat stack of résumés "
            "on the seat and a sleek laptop set aside on the floor — experience valued over youth."
        )
    if any(k in text for k in ("voice", "clone", "cloning", "speech", "audio", "weights")):
        return (
            "A studio microphone on a desk stand with curved sound-wave lines beside it, "
            "and a small cardboard acquisition box with a bow — an AI voice startup purchase."
        )
    if _is_social_coding_story(text):
        return _safe_social_coding_scene()
    if any(k in text for k in ("openai", "anthropic", "google")) and any(
        k in text for k in ("bought", "acquire", "acquisition", "deal", "merger")
    ):
        return (
            "Two company-logo-free cardboard boxes on a table, the smaller one with a "
            "microphone on top being slid toward a larger box — big tech buying a specialist startup."
        )
    if any(
        k in text
        for k in (
            "generative video", "interactive video", "diffusion model", "diffusion",
            "real-time video", "real time video", "4 steps", "four steps",
        )
    ) or ("video" in text and any(k in text for k in ("frame", "frames", "render", "generated"))):
        return (
            "A film strip showing four sequential frames of a simple shape in motion, "
            "next to a large play-button triangle — fast generative video."
        )
    if any(
        k in text
        for k in ("codex", "coding app", "coding agent", "on your phone", "to mobile", "ios", "android")
    ):
        return (
            "A smartphone with a blank dark screen leans against a small portable "
            "keyboard on a bench, seen from above — coding on the go."
        )
    if any(
        k in text
        for k in ("bank account", "personal finance", "spending", "budget", "plaid", "credit card", "financial")
    ):
        return (
            "A ceramic piggy bank on a sunny windowsill with a magnifying glass leaning "
            "toward the coin slot — personal finance under scrutiny."
        )
    if any(
        k in text
        for k in ("trial", "lawsuit", "court", "nonprofit", "musk", "founder", "charter", "for-profit")
    ):
        return (
            "On a courtroom bench: two rolled blank charters side by side — one sealed "
            "with a simple heart-shaped wax stamp (nonprofit mission), one with a coin-shaped "
            "wax stamp (for-profit pivot) — with a wooden gavel resting between them."
        )
    if "pull request" in text or "code review" in text:
        return (
            "A smartphone with a blank dark screen beside a small notebook and stylus "
            "on a bench — quick code review or prompt work on the go."
        )
    return (
        f"Simple editorial still life that clearly illustrates: {headline}. "
        "Two or three everyday objects a reader would associate with this news topic. "
        "Obvious and literal, not abstract or surreal."
    )


def _is_generic_curated(scene: str) -> bool:
    return scene.startswith("Simple editorial still life that clearly illustrates:")


def _llm_scene(
    client: Any | None,
    headline: str,
    blurb: str,
    why: str,
    slot: str,
    profile: dict[str, str],
) -> str | None:
    prompt = (
        f"Headline: {headline}\n"
        f"Blurb: {blurb}\n"
        f"Why it matters: {why}\n"
        f"Audience slot: {slot or 'general'}\n"
        f"Required composition: {profile['composition']}\n"
        f"Required mood: {profile['mood']}\n\n"
        "Write the scene brief now. Clarity beats cleverness."
    )
    try:
        from espresso_agent import call_llm_json  # type: ignore
        out = call_llm_json(client, SCENE_SYSTEM, prompt, SCENE_SCHEMA, max_tokens=700)
        scene = (out or {}).get("scene", "").strip()
        return scene or None
    except Exception as e:
        print(f"  [scene-llm fail] {e}", file=sys.stderr)
        return None


def _sanitize_scene(scene: str) -> str:
    """Drop LLM habits that make every card look the same or miss the story."""
    banned = (
        "coffee cup", "espresso", "café table", "cafe table", "ethernet", "still-life trio",
        "compass", "neon dollar", "split compass",
        "piggy bank", "microwave", "clapperboard", "film clapper", "wig", "tangled hair",
        "spilling out of a briefcase",
    )
    lower = scene.lower()
    if any(b in lower for b in banned):
        return ""
    return scene


def _resolve_scene(
    client: Any | None,
    headline: str,
    blurb: str,
    why: str,
    slot: str,
    card_index: int,
    profile: dict[str, str],
) -> str:
    """Prefer agent-written scene brief; fall back to keyword still-life."""
    if _is_social_coding_story(f"{headline} {blurb}".lower()):
        return _safe_social_coding_scene()
    if client is not None:
        llm = _sanitize_scene(_llm_scene(client, headline, blurb, why, slot, profile) or "")
        if llm and len(llm) > 40:
            return llm
    curated = _curated_scene(headline, blurb, slot, card_index)
    if not _is_generic_curated(curated):
        return curated
    return curated


def _resolve_prompt_scene(client: Any | None, prompt_card: dict[str, Any]) -> str:
    """Scene for the prompt tile — derived from that day's generated prompt card copy."""
    title = (prompt_card.get("title") or "Try this prompt").strip()
    kicker = (prompt_card.get("kicker") or "").strip()
    body = (prompt_card.get("prompt") or "").strip()
    text = f"{title} {kicker} {body}".lower()
    if any(k in text for k in ("explainer", "plain-english", "plain english", "jargon")):
        return (
            "A thick open dictionary beside a magnifying glass on a plain surface — "
            "making technical language clear, no people or text on the pages."
        )
    if any(k in text for k in ("meeting", "recap", "memo", "decision")):
        return (
            "A notepad with a pen beside a simple checklist on a desk — "
            "capturing decisions and next steps, no people."
        )
    if any(k in text for k in ("spending", "budget", "audit", "expense")):
        return (
            "A receipt beside a simple calculator on a plain surface — "
            "reviewing spending, no people or readable numbers."
        )
    blurb = f"{kicker}\n{body}"[:500]
    profile = CARD_PROFILES[3]
    return _resolve_scene(client, title, blurb, kicker, "prompt", 3, profile)


def _strip_image_trigger_phrases(prompt: str) -> str:
    """Remove brand names and moderation triggers from the final image prompt."""
    replacements = (
        ("Grok", "AI assistant"),
        ("grok", "AI assistant"),
        (" xAI", ""),
        ("xAI", "AI lab"),
        (" X posts", " social posts"),
        (" X post", " social post"),
        (" on X", " on a social network"),
        ("deploy code", "publish code"),
        ("code deployment", "publishing code"),
        ("social platform", "social feed"),
    )
    for src, dst in replacements:
        prompt = prompt.replace(src, dst)
    return prompt


def _build_image_prompt(
    scene: str,
    profile: dict[str, str],
    aspect: str,
) -> str:
    """Keep prompts short — long envelopes trigger OpenAI image moderation."""
    ratio = "16:9" if aspect == "16:9" else "1:1 square"
    scene = _strip_image_trigger_phrases(scene)
    return _strip_image_trigger_phrases(
        (
            f"{ILLUSTRATION_STYLE} {scene} {ratio}. "
            "Clear readable composition, obvious subject, engraving shading visible at thumbnail size. "
            "No people, no faces, no text, no logos. Duotone slate-blue ink on warm cream only."
        )[:900]
    )


def _prompt_for_story(
    story: dict[str, Any],
    client: Any | None,
    card_index: int,
) -> str:
    profile = CARD_PROFILES[min(card_index, 2)]
    headline = (story.get("headline") or "").strip()
    blurb = (story.get("blurb") or "").strip()
    why = (story.get("why_it_matters") or "").strip()
    slot = (story.get("slot") or "").strip()
    scene = _resolve_scene(client, headline, blurb, why, slot, card_index, profile)
    return _build_image_prompt(scene, profile, "1:1")


def _prompt_for_card(
    prompt_card: dict[str, Any],
    client: Any | None,
) -> str:
    profile = CARD_PROFILES[3]
    scene = _resolve_prompt_scene(client, prompt_card)
    return _build_image_prompt(scene, profile, "1:1")


def _illustration_ok(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 10_000


def _write_placeholder_png(path: Path, aspect_ratio: str) -> bool:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("  [skip] Pillow not installed; cannot write placeholder", file=sys.stderr)
        return False

    w, h = (800, 450) if aspect_ratio == "16:9" else (600, 600)
    img = Image.new("RGB", (w, h), "#f5f0e8")
    draw = ImageDraw.Draw(img)
    draw.rectangle([24, 24, w - 24, h - 24], outline="#6b4423", width=4)
    draw.ellipse([w // 2 - 28, h // 2 - 28, w // 2 + 28, h // 2 + 28], outline="#8b5a2b", width=3)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path.exists()


def _fallback_placeholder(path: Path, aspect_ratio: str) -> bool:
    """Write a dev preview placeholder but report failure for CI/production gates."""
    _write_placeholder_png(path, aspect_ratio)
    return False


def _find_asi_cli() -> str | None:
    cli = shutil.which("asi-generate-image")
    if cli:
        return cli
    bundled = Path(__file__).resolve().parent / "bin" / "asi-generate-image"
    if bundled.is_file():
        return str(bundled)
    return None


def _run_gemini_inprocess(prompt: str, out_path: Path, aspect_ratio: str) -> bool:
    """Call Gemini directly (avoids subprocess ASCII stderr issues on some macOS shells)."""
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return False
    try:
        import importlib.util

        cli_path = Path(__file__).resolve().parent / "bin" / "asi-generate-image"
        spec = importlib.util.spec_from_file_location("asi_generate_image", cli_path)
        if spec is None or spec.loader is None:
            return False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._generate_gemini(prompt, out_path, aspect_ratio)
        return out_path.exists() and out_path.stat().st_size > 10_000
    except Exception as e:
        print(f"  [gemini in-process fail] {type(e).__name__}: {e!r}", file=sys.stderr)
        return False


def _run_image_cli(prompt: str, filename_no_ext: Path, aspect_ratio: str) -> bool:
    cli = _find_asi_cli()
    out_path = Path(str(filename_no_ext) + ".png")
    safe_prompt = _normalize_prompt_for_cli(prompt)

    if _illustration_ok(out_path):
        print(f"  [skip] keeping existing {out_path.name}", file=sys.stderr)
        return True

    if _run_gemini_inprocess(safe_prompt, out_path, aspect_ratio):
        print("  backend: gemini (nano banana)", file=sys.stderr)
        return True

    if not cli:
        print("  [skip] asi-generate-image not on PATH; using placeholder", file=sys.stderr)
        return _fallback_placeholder(out_path, aspect_ratio)

    payload = json.dumps({
        "prompt": safe_prompt,
        "filename": str(filename_no_ext),
        "aspect_ratio": aspect_ratio,
        "model": "nano_banana_2",
    }, ensure_ascii=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        result = subprocess.run(
            [cli, payload],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=env,
        )
        if result.returncode != 0:
            print(f"  [error] {result.stderr[:300]}", file=sys.stderr)
            return _fallback_placeholder(out_path, aspect_ratio)
        if _illustration_ok(out_path):
            return True
        return _fallback_placeholder(out_path, aspect_ratio)
    except subprocess.TimeoutExpired:
        print("  [timeout] image gen exceeded 180s", file=sys.stderr)
        return _fallback_placeholder(out_path, aspect_ratio)
    except Exception as e:
        print(f"  [exception] {e}", file=sys.stderr)
        return _fallback_placeholder(out_path, aspect_ratio)


def _build_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore
        return anthropic.Anthropic()
    except Exception:
        return None


# Card thumbnails display at 160px; 512px covers 3x retina with headroom for email.
EDITION_PNG_MAX_WIDTH = 512


def compress_edition_pngs(
    image_paths: list[Path],
    *,
    max_width: int = EDITION_PNG_MAX_WIDTH,
) -> dict[str, Any]:
    """
    Resize oversized illustration PNGs and re-save with optimize=True.
    Skips paths that are missing or when Pillow is unavailable.
    """
    try:
        from PIL import Image
    except ImportError:
        return {
            "compressed": [],
            "skipped": [str(p) for p in image_paths],
            "reason": "pillow_unavailable",
        }

    compressed: list[str] = []
    skipped: list[str] = []
    for raw in image_paths:
        path = Path(raw)
        if not path.is_file():
            skipped.append(str(path))
            continue
        before = path.stat().st_size
        try:
            with Image.open(path) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                w, h = img.size
                if max(w, h) > max_width:
                    scale = max_width / float(max(w, h))
                    new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
                    resample = getattr(Image, "Resampling", Image).LANCZOS
                    img = img.resize(new_size, resample)
                path.parent.mkdir(parents=True, exist_ok=True)
                img.save(path, format="PNG", optimize=True)
            after = path.stat().st_size
            compressed.append(str(path))
            print(
                f"  [compress] {path.name}: {before // 1024}KB → {after // 1024}KB",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"  [compress fail] {path}: {e}", file=sys.stderr)
            skipped.append(str(path))
    return {"compressed": compressed, "skipped": skipped}


def render_images(
    render_result: dict[str, Any],
    edition_data: dict[str, Any],
) -> dict[str, Any]:
    image_paths = render_result["image_paths"]
    stories = edition_data.get("stories", [])[:3]
    prompt_card = edition_data.get("try_this_prompt") or {}

    client = _build_client()
    generated, missing, prompts = [], [], []

    for i, (story, path) in enumerate(zip(stories, image_paths[:3]), start=0):
        no_ext = Path(str(path).removesuffix(".png"))
        print(f"  [{i + 1}/4] {story.get('headline', '')[:60]}...", file=sys.stderr)
        profile = CARD_PROFILES[min(i, 2)]
        prompt = _prompt_for_story(story, client, i)
        prompts.append(prompt)
        ok = _run_image_cli(prompt, no_ext, "1:1")
        if not ok and _is_social_coding_story(
            f"{story.get('headline', '')} {story.get('blurb', '')}".lower()
        ):
            safe_prompt = _build_image_prompt(_safe_social_coding_scene(), profile, "1:1")
            print("  [retry] card 3 safe brand-free scene", file=sys.stderr)
            prompts[-1] = safe_prompt
            ok = _run_image_cli(safe_prompt, no_ext, "1:1")
        (generated if ok else missing).append(str(path))

    if len(image_paths) >= 4:
        path = image_paths[3]
        no_ext = Path(str(path).removesuffix(".png"))
        print(f"  [4/4] prompt: {prompt_card.get('title', '')[:60]}...", file=sys.stderr)
        prompt = _prompt_for_card(prompt_card, client)
        prompts.append(prompt)
        ok = _run_image_cli(prompt, no_ext, "1:1")
        (generated if ok else missing).append(str(path))

    compress_result = compress_edition_pngs([Path(p) for p in generated])
    return {
        "generated": generated,
        "missing": missing,
        "prompts": prompts,
        "compress": compress_result,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: render_images.py <render_result_json> <edition_json>")
        sys.exit(1)
    rr = json.loads(Path(sys.argv[1]).read_text())
    rr["image_paths"] = [Path(p) for p in rr["image_paths"]]
    ed = json.loads(Path(sys.argv[2]).read_text())
    result = render_images(rr, ed)
    print(json.dumps(result, indent=2))
