# AI Espresso — Pre-launch context

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
- [ ] Phase 1.2 QOTD honest UX
- [ ] Phase 1.3 CI/workflow guards + alerts
- [ ] Phase 2.1 Unified rubric in prompts
- [ ] Phase 2.2 Shrink constitution / HEADLINE_HARD_SKIP_RE
- [ ] Phase 2.3 Deterministic path dev-only / aligned
- [ ] Phase 3 Editorial quality pass + clean prod edition
- [ ] Phase 4 External polish

## 2026-05-18 — Pre-launch audit summary

**Blockers:** duplicate `archive.jsonl` per day; no dev archive skip; QOTD fake API success; hardcoded editorial regex in `constitution.py` / `editorial.py`.

**High:** footer personal email + stale repo URL; `RANKING_SYSTEM` vs agent audience mismatch; render exit 0 with missing images; ~2.5MB PNGs; `run_chain.py` doc missing; daily cron skips tests.

**Working well:** native tool_use loop, approve→ship lock, constitution gate vs bad critic approve, full agent_trace, 64 tests passing.

**Sample edition:** `2026-05-18` / edition_4 — agent shipped after critic revise; trace at `agent/data/editions/2026-05-18.json`.
