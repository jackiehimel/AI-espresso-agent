# AI Espresso — Pre-launch context

**GitHub:** https://github.com/jackiehimel/ai-espresso-finalized (private)  
**Baseline commit:** `a776f26` — tag with `git tag pre-launch-baseline` to revert anytime.

## 2026-05-18 — Execution playbook (fix order without context rot)

**Verdict:** Not launch-ready. Agent loop is real (native `tool_use` Editor, ship gates, trace). Fix ops/trust first, then rubric-in-prompts, never expand regex constitution.

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

- [ ] Phase 1.1 Archive upsert + ESPRESSO_SKIP_ARCHIVE
- [x] Phase 1.2 QOTD honest UX — merged `10e4dfc` (with 1.3 in same PR)
- [x] Phase 1.3 CI/workflow guards + alerts — merged `10e4dfc`
- [x] Phase 2.1 Unified rubric in prompts — merged `b4a8563`
- [x] Phase 2.2 Shrink constitution / HEADLINE_HARD_SKIP_RE — merged `5b9e7c4`
- [x] Phase 2.3 Deterministic path dev-only / aligned — merged `98ba480`
- [ ] Phase 3 Editorial quality pass + clean prod edition
- [ ] Phase 4 External polish

### Session handoff (Phase 2.3 — deterministic fallback dev-only) — MERGED

**Tests (2026-05-18):** `82` ran, `OK` (`2` skipped). Command: `cd agent && python3 -m unittest discover -s tests -p "test_*.py" -v`.

**Shipped in `docs: deterministic fallback dev-only`:**
- `RANKING_SYSTEM` deprecation block: not synced with `_EDITORIAL_RUBRIC`; never enable in `daily-edition.yml`.
- `agent/README.md`: deterministic fallback table; fixed misleading “always falls back” copy.
- `daily-edition.yml`: production comment — agent loop only (no legacy fallback env).
- `test_daily_workflow.py`: CI workflow must use `mode='agent'` only; no fallback env in non-comment lines.

**Phase 2 complete.** Agent strength track done (2.1 rubric, 2.2 constitution backstop, 2.3 dev-only deterministic).

**Next session:** **Prompt 6** — Phase **3** (editorial quality + clean prod edition) *or* Phase **4** (polish). Pick one per PR. Phase **1.1** archive upsert still open if doing ops first.

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

**Still open from audit:** Phase 1.1 archive ops; footer email / repo URL; large PNGs; `run_chain.py` doc.

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

**Blockers:** duplicate `archive.jsonl` per day; no dev archive skip.

**High:** footer personal email + stale repo URL; ~2.5MB PNGs; `run_chain.py` doc missing.

**Resolved (1.2/1.3):** QOTD fake API success; render exit 0 with missing images; daily cron skips tests.

**Resolved (2.1):** unified Scout/Editor/Critic rubric in prompts (`_EDITORIAL_RUBRIC`); audience/partnership/deepfake distinctions in prompt text, not new code regex.

**Resolved (2.2):** constitution / `HEADLINE_HARD_SKIP_RE` narrowed to empty headline, AI lexicon, obvious failure-as-primary; sociology/PR/drama/office openings prompt-led.

**Resolved (2.3):** deterministic fallback documented dev-only; `daily-edition.yml` never sets fallback env; `RANKING_SYSTEM` marked deprecated vs agent rubric.

**Working well:** native tool_use loop, approve→ship lock, constitution gate vs bad critic approve, full agent_trace, **82 tests** (2 skipped).

**Sample edition:** `2026-05-18` / edition_4 — agent shipped after critic revise; trace at `agent/data/editions/2026-05-18.json`.
