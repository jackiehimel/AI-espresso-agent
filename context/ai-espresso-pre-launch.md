# AI Espresso — Pre-launch context

**GitHub:** https://github.com/jackiehimel/ai-espresso-finalized (private)  
**Baseline commit:** `a776f26` — tag with `git tag pre-launch-baseline` to revert anytime.

## Launch readiness (2026-05-18)

**Verdict:** Code and CI are launch-ready. **Daily cron is blocked until GitHub Actions secrets are configured** (see below).

| Gate | Status |
|------|--------|
| Unit tests | **98** ran, **OK**, **0** skipped — `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v` |
| CI (`Tests` workflow) | Green on `03d96b7` — https://github.com/jackiehimel/ai-espresso-finalized/actions/runs/26053716886 |
| `origin/main` | `03d96b7` (includes `991f54c` archive upsert, `7a49d5f` compact reads, illustration gate) |
| Archive | Single row per date; `load_archive` / `recent_archive_headlines` use compacted reads |
| Verification edition | `agent/data/editions/2026-05-19.json`; HTML `editions/edition_4_variant_c.html` |
| Illustrations | Production PNGs on edition 4 (~500KB each); `render_edition.py 2026-05-19` exits **0** without `--allow-missing-images` |
| `workflow_dispatch` smoke | Ran 2026-05-19 `skip_email=true` — https://github.com/jackiehimel/ai-espresso-finalized/actions/runs/26053564938 — **unit tests passed**; **agent step failed** (`ANTHROPIC_API_KEY` not set in repo secrets) |

**Before enabling the daily schedule**, add repository secrets used by [`.github/workflows/daily-edition.yml`](.github/workflows/daily-edition.yml):

- `ANTHROPIC_API_KEY` (required — Scout + Editor loop)
- `GEMINI_API_KEY` or `GOOGLE_API_KEY` (illustrations)
- `OPENAI_API_KEY` (optional illustration fallback)
- `PERPLEXITY_API_KEY` (search tool)
- `AI_ESPRESSO_FROM`, `AI_ESPRESSO_TO`, `GMAIL_APP_PASSWORD` (email; optional on first run with `skip_email: true`)
- `SLACK_WEBHOOK_URL` (optional failure alerts)

Re-run: `gh workflow run "Daily AI Espresso edition" --ref main -f date=2026-05-19 -f skip_email=true`

## 2026-05-18 — Execution playbook (fix order without context rot)

**Verdict:** Pre-launch engineering complete; enable cron after secrets + one green `workflow_dispatch`.

### Agent non-negotiables (do not de-agent)

- Model drives editorial loop via tools: `read_candidate`, `pick`, `search_news`, `self_critique`, `ship_edition`.
- Python enforces **rails only**: budget, vendor cap, tier-1 min, verified body, archive dedupe, narrow constitution backstop.
- **Do not:** expand `constitution.py` / `HEADLINE_HARD_SKIP_RE` as primary editor; default CI to `rank_and_select`; pre-filter candidates before Scout; auto-ship without `ship_edition`.
- Deterministic fallback: `ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK=1` dev-only; align or gut `RANKING_SYSTEM` so it is not a second product.
- Show Venu/clients: `agent_trace` (scout → tools → critic revise → ship), not HTML polish alone.

### Fix phases (order)

1. **Ops integrity:** archive upsert-by-date + `ESPRESSO_SKIP_ARCHIVE`; preview defaults `dry_run`; clean duplicate `2026-05-18` archive rows; QOTD honest (no fake success); CI fail on missing images, tests before daily job, failure alerts.
2. **Agent strength:** single rubric in Scout/Editor/Critic prompts; shrink code regex grievance lists; emergency-only deterministic path.
3. **Quality (prompt-led):** critic/editor bar for partnerships, outlet diversity, deepfake-scandal vs detection-product; one clean prod run with single archive row.
4. **Polish:** footer Solvd contact, hide T1 on public HTML, compress PNGs, README `run_chain.py` fix, optional masthead voice change.

### How to execute without degrading quality

- **Skill:** `.cursor/skills/ai-espresso-agent/` — attach every session.
- **Copy-paste prompts:** `context/ai-espresso-session-prompts.md` (Prompts 1–7 in order).
- **One phase per PR** (or one PR per numbered item in phase 1–2). Merge before starting next phase.
- **Start each session:** read this file + run `cd agent && python3 -m unittest discover -s tests -p test_*.py"`.
- **End each session:** update "Completed" below; paste test count; note any rubric prompt diff intent.
- **Per-PR prompt to agent:** scope to one phase; link this file; repeat agent non-negotiables; forbid drive-by refactors.
- **Verification gate before "done":** tests green; for agent-touching PRs, inspect `agent_trace` on a dry run or fixture; for render PRs, open HTML locally.
- **Do not** batch "all audit fixes" in one thread — context window rot is the main risk.

### Completed (check off as merged)

- [x] Phase 1.1 Archive upsert + ESPRESSO_SKIP_ARCHIVE — pushed `991f54c`, read compaction `7a49d5f`
- [x] Phase 1.2 QOTD honest UX — merged `10e4dfc` (with 1.3 in same PR)
- [x] Phase 1.3 CI/workflow guards + alerts — merged `10e4dfc`
- [x] Phase 2.1 Unified rubric in prompts — merged `b4a8563`
- [x] Phase 2.2 Shrink constitution / HEADLINE_HARD_SKIP_RE — merged `5b9e7c4`
- [x] Phase 2.3 Deterministic path dev-only / aligned — merged `98ba480`
- [x] Phase 3 Editorial quality pass + clean prod edition — verified 2026-05-19 local run
- [x] Phase 4 External polish — merged `bd43630`

### Session handoff (Phase 4 — public edition polish) — MERGED

**Tests (2026-05-18):** `85` ran, `OK` (`3` skipped). Command: `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v`.

**Shipped in `polish: public edition HTML and assets`:**
- `render_html.py`: Solvd footer (`jhimel@solvd.com`), repo `ai-espresso-finalized`; no `T1`/`T2` in public HTML.
- `render_images.py`: `compress_edition_pngs` (max width 512, `optimize=True`) after illustration gen (~2.5MB → ~500KB per card on edition_4).
- `agent/README.md`: dev backfill shell loop; removed missing `run_chain.py` references.
- `editorial.py`: neutral `VOICE_CHARACTERS` (no Marvel/film IPs).
- `agent/tests/test_render_polish.py`: footer, hidden tiers, compress helper.
- `editions/edition_4_*`: regenerated HTML/MD + compressed assets.

**Preview:** `cd agent && python3 preview_edition.py 2026-05-18 --render-only --no-images` (serves `editions/`; images at 160px CSS from 512px PNGs).

### Session handoff (Phase 1.1 — archive upsert + preview safety) — MERGED

**Tests (2026-05-18):** `98` ran, `OK` (`0` skipped).

**Shipped in `fix: harden archive and preview reliability` (`991f54c`) + `fix: compact archive reads` (`7a49d5f`):**
- `append_archive`: upsert by edition date; compact duplicate rows on write.
- `ESPRESSO_SKIP_ARCHIVE=1`: skip archive in `write_edition`; preview defaults skip unless `--write-archive`.
- `preview_edition.py`: sets skip env during agent preview; `--write-archive` opt-in.
- `load_archive` / `recent_archive_headlines`: read via `_load_archive_records_compacted()` (last row per date wins).
- CI: optional flaky RSS feeds on GHA; optional Slack on workflow failure.
- `agent/data/archive.jsonl`: single `2026-05-18` row aligned with shipped edition JSON.

### Session handoff (Phase 3 — editorial quality verification) — LOCAL

**Tests (2026-05-18):** `85` ran, `OK` (`3` skipped). Command: `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v`.

**Agent run:** `ESPRESSO_SKIP_ARCHIVE=1 python3 espresso_agent.py --date 2026-05-19 --use-cache --mode agent` (~3 min). Edition JSON: `agent/data/editions/2026-05-19.json`. Archive unchanged (still two `2026-05-18` rows only).

**Minimal ops for run:** `write_edition` respects `ESPRESSO_SKIP_ARCHIVE=1` (skip `append_archive`); full Phase 1.1 upsert/tests still open.

**agent_trace (64 events):** scout (shortlist=12) → editor `read_candidate`/`pick`/`unpick`/`search_news` → `self_critique` **revise** ×2 (OpenAI overlap + Anthropic vendor cap) → `self_critique` **approve** → `ship_edition`.

**Headlines shipped:**
- business: Anthropic just signed a compute deal with SpaceX (Anthropic News)
- beginner: ChatGPT can now write and run code directly on your phone (9to5Mac — AI)
- cross: OpenAI insiders say Apple's ChatGPT integration is a letdown (Ars Technica — AI)

**Render:** `editions/edition_4_variant_c.html` (issue 4 slot). QOTD: honest preview copy. No `T1`/`T2` in public HTML. Illustrations: production PNGs restored/compressed on edition 4 (~500KB each); story-matched regen needs `GEMINI_API_KEY` / `OPENAI_API_KEY` in `agent/.env` or GHA secrets.

**Quality:** No prompt changes — critic loop corrected vendor overlap and archive-adjacent finance repeat without new regex.

**Preview:** `python3 preview_edition.py 2026-05-19 --render-only`

### Session handoff (Phase 2.3 — deterministic fallback dev-only) — MERGED

**Tests (2026-05-18):** `82` ran, `OK` (`2` skipped). Command: `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v`.

**Shipped in `docs: deterministic fallback dev-only`:**
- `RANKING_SYSTEM` deprecation block: not synced with `_EDITORIAL_RUBRIC`; never enable in `daily-edition.yml`.
- `agent/README.md`: deterministic fallback table; fixed misleading “always falls back” copy.
- `daily-edition.yml`: production comment — agent loop only (no legacy fallback env).
- `test_daily_workflow.py`: CI workflow must use `mode='agent'` only; no fallback env in non-comment lines.

**Phase 2 complete.** Agent strength track done (2.1 rubric, 2.2 constitution backstop, 2.3 dev-only deterministic).

### Session handoff (Phase 2.2 — narrow constitution backstop)

**Tests (2026-05-18):** `79` ran, `OK` (`2` skipped). Command: `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v`.

**Shipped (local, PR title `refactor: narrow constitution to ship backstop`):**
- `constitution.py`: removed `HARD_REJECT_PATTERNS`, `NO_HOOK_RE`, `INCIDENTAL_FAILURE_RE`, broad `AI_FAILURE_TONE_RE` / layoff lexicon; kept `AI_LEXICON_RE` + narrow `FAILURE_PRIMARY_RE` (slop, glitch, trapped, fails again, refuses to help).
- `editorial.py`: `HEADLINE_HARD_SKIP_RE` aliases `FAILURE_PRIMARY_RE`; removed `DRAMA_HEADLINE_RE`.
- Tests document prompt-led cases (HBR, office opening, legal drama) vs code backstop (Waymo trapped, slop, refuses).

**Next session:** (done) Phase 2.3 merged — see handoff above.

### Session handoff (after PR merge `b4a8563` — Phase 2.1)

**Tests (2026-05-18):** `73` ran, `OK` (`2` skipped). Command: `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v`.

**Shipped in `refactor: unify agent editorial prompts`:**
- `_EDITORIAL_RUBRIC` in `espresso_loop.py` shared by Scout/Editor/Critic (role-specific tails only).
- Audience: any Solvd employee (not non-technical-only). Lab partnerships OK with hook; HBR/workforce sociology reject; deepfake scandal vs detection-product feature clarified.
- `constitution_prompt_block()` still appended to all three system prompts; tool loop and `dispatch_tool` gates unchanged.

**Next session:** **Prompt 5** — Phase **2.3 only** (deterministic fallback dev-only). See handoff above for 2.2.

**Still open from audit:** Phase 1.1 archive ops.

**Resolved (4):** footer Solvd contact + `ai-espresso-finalized` repo URL; source tiers hidden in public HTML; PNG compress on render; README no longer references missing `run_chain.py`; editorial `VOICE_CHARACTERS` (no Marvel IPs).

**Resolved (2.3):** `RANKING_SYSTEM` documented as deprecated dev-only (not second product); production workflow guarded.

### Session handoff (after PR merge `10e4dfc`)

**Tests (2026-05-18):** `73` ran, `OK` (`2` skipped fixtures). Command: `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v`.

**Shipped in `fix: QOTD and daily workflow guards`:**
- QOTD: default static editions show honest preview copy (no form / fake “Thanks — recorded”). Set `AI_ESPRESSO_QOTD_API_URL` at render time for hosted submit (`{base}/api/daily-question`); success only on `res.ok`.
- `render_edition.py`: exit `1` when illustrations missing unless `--allow-missing-images`.
- `daily-edition.yml`: unit tests before generate; render JSON outputs → email paths; `notify-failure` job writes step summary.
- New tests: `test_qotd.py`, `test_render_edition.py`. `edition_4_variant_c.html` regenerated with preview QOTD.

**Historical note:** 1.1 archive ops still open; 2.1 rubric merged in `b4a8563` (see handoff above).

## 2026-05-18 — Pre-launch audit summary

**Resolved blockers:** duplicate `archive.jsonl` per day; no dev archive skip; QOTD fake success; render exit 0 with missing/placeholder images; daily cron skips tests; CI red on source fetch (RSS migration + flaky-feed tolerance).

**Resolved (4):** footer personal email + stale repo URL; ~2.5MB PNGs; `run_chain.py` doc missing.

**Resolved (1.1):** archive upsert + `ESPRESSO_SKIP_ARCHIVE` + preview `--write-archive`; compacted archive reads.

**Resolved (1.2/1.3):** honest QOTD; tests-before-generate; failure summary + optional Slack.

**Resolved (2.1–2.3):** unified rubric; narrow constitution backstop; deterministic path dev-only.

**Remaining (ops, not code):** configure GitHub Actions secrets; re-run `workflow_dispatch` until `generate` job succeeds; optional story-matched illustration regen when keys are set.

**Working well:** native tool_use loop, approve→ship lock, constitution gate vs bad critic approve, full agent_trace, **98 tests** (0 skipped).

**Sample editions:** `2026-05-18` (fixture) / `2026-05-19` (verification run) — traces in `agent/data/editions/`.
