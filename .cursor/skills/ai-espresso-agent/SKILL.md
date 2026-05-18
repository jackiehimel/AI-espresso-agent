---
name: ai-espresso-agent
description: >-
  Work on AI Espresso pre-launch fixes while preserving native tool_use agent
  architecture. Use when editing agent/, espresso_loop, constitution, CI
  workflows, or when the user mentions AI Espresso launch, audit fixes, or
  agent integrity. Requires reading context/ai-espresso-pre-launch.md first.
---

# AI Espresso — Agent integrity skill

## Before any work

1. Read `context/ai-espresso-pre-launch.md` (phases, checklist, audit summary).
2. If the user gave a session prompt from `context/ai-espresso-session-prompts.md`, follow **only** that scope.
3. Run tests: `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v`

## Agent non-negotiables (do not de-agent)

AI Espresso **must qualify as an AI agent**, not a pipeline with an LLM garnish.

- The **model** drives the editorial loop via Anthropic **native `tool_use`**: `read_candidate`, `pick`, `unpick`, `search_news`, `check_archive`, `update_memory`, `note_weak_pool`, `self_critique`, `ship_edition`.
- **Python** enforces **rails only**: tool budgets, vendor cap, tier-1 minimum, verified body, archive dedupe, narrow constitution backstop on ship.
- **Scout** may stay one-shot JSON; **Editor** must remain the `tool_use` loop in `espresso_loop.py`. Do not replace it with JSON plan dispatch.

### Never do (pipeline traps)

- Expand `constitution.py` or `HEADLINE_HARD_SKIP_RE` with new headline-specific regexes as the primary way to fix editorial quality.
- Pre-filter the candidate pool in Python before Scout sees stories (beyond existing dedupe/cap).
- Default CI or production to `rank_and_select` / `mode=deterministic` without explicit dev-only env flag.
- Auto-ship when gates pass **without** the model calling `ship_edition`.
- Remove or bypass `self_critique` approve → `ship_edition` lock in `dispatch_tool`.
- Batch unrelated audit fixes in one PR or one session.

### Allowed improvements (more agentic)

- Unify Scout / Editor / Critic **prompts** (single rubric, no contradictions).
- **Shrink** code-level grievance regex; keep constitution as a small backstop.
- Improve tool descriptions, working memory, trace, failure artifacts.
- Ops: archive upsert, `ESPRESSO_SKIP_ARCHIVE`, honest QOTD, CI gates — no change to who picks stories.

## After work

1. Tests must pass (report count).
2. Update checkboxes in `context/ai-espresso-pre-launch.md` for completed phases.
3. For agent/rubric changes: confirm `agent_trace` still shows tool loop → `self_critique` → `ship_edition` (fixture or described dry run).
4. Do not claim done without evidence.

## Reference

- Session prompts (exact order): `context/ai-espresso-session-prompts.md`
- Launch playbook: `context/ai-espresso-pre-launch.md`
