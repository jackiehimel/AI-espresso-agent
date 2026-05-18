# AI Espresso — Session prompts (use in order)

Use **one prompt per chat**. Attach skill: **ai-espresso-agent** (or type `/ai-espresso-agent` if configured).

Merge each PR before starting the next prompt.

---

## Prompt 0 — Baseline (already done if repo exists)

```
Use the ai-espresso-agent skill. Read context/ai-espresso-pre-launch.md.

Initialize git for this workspace if needed: create GitHub repo, initial commit of current
state (respect .gitignore). Do not change application code. Report remote URL and default branch.
```

---

## Prompt 1 — PR1: Archive & dev isolation

```
Use the ai-espresso-agent skill. Read context/ai-espresso-pre-launch.md.

Implement Phase 1.1 only:

- Upsert archive.jsonl by edition date (one record per day), do not append duplicate dates.
- Add ESPRESSO_SKIP_ARCHIVE=1 to skip append_archive in write_edition.
- preview_edition.py: default dry_run=True for agent unless --write-archive; document flags.
- Dedupe/clean existing duplicate 2026-05-18 rows in agent/data/archive.jsonl to match the shipped edition_4 stories only.
- Add tests for upsert and skip-archive behavior.

Do NOT touch Scout/Editor/Critic prompts, constitution regex, render HTML, or CI yet.
Run full test suite before finishing. Update checklist in context/ai-espresso-pre-launch.md.
Open a PR titled: fix: archive upsert and dev skip flag
```

---

## Prompt 2 — PR2: QOTD + CI

```
Use the ai-espresso-agent skill. Read context/ai-espresso-pre-launch.md.

Implement Phase 1.2 and 1.3 only:

**QOTD:** Stop fake success — either remove submit + "Thanks — recorded" for static editions,
or gate fetch behind a real base URL env var, or show honest "preview only" copy. No silent .catch that lies.

**CI:** daily-edition.yml — run unit tests before generate; render_edition fails non-zero if images missing
(unless --allow-missing-images); add a workflow failure notification step (GitHub summary or slack placeholder comment).
Pass edition issue/date from render step to email step instead of ls | tail -1.

Do NOT change agent prompts or constitution. Run tests. Update checklist.
PR title: fix: honest QOTD and daily workflow guards
```

---

## Prompt 3 — PR3: Unified rubric (prompts only)

```
Use the ai-espresso-agent skill. Read context/ai-espresso-pre-launch.md.

Implement Phase 2.1 only:

- Unify SCOUT_SYSTEM, EDITOR_SYSTEM, CRITIC_SYSTEM in espresso_loop.py to one consistent rubric.
- Audience: "any Solvd employee" (not "non-technical person" only).
- Clarify: lab partnerships OK with hook; workforce sociology / HBR think pieces reject;
  deepfake scandal vs deepfake-detection product feature.
- Remove contradictory duplicate blocks; keep constitution_prompt_block() injection.

Do NOT add new regex to constitution.py or editorial.py.
Do NOT change tool loop structure or dispatch_tool gates.
Run tests. Update checklist.
PR title: refactor: unify agent editorial prompts
```

---

## Prompt 4 — PR4: Shrink code backstop

```
Use the ai-espresso-agent skill. Read context/ai-espresso-pre-launch.md.

Implement Phase 2.2 only:

- Shrink HARD_REJECT_PATTERNS, FAILURE_PRIMARY_RE, HEADLINE_HARD_SKIP_RE to a narrow backstop
  (e.g. empty headline, AI not load-bearing, obvious failure-as-primary-frame).
- Remove headline-specific grievance strings (PwC expands, trapped in atlanta, etc.) from code.
- Move editorial judgment into prompts (already unified in PR3).
- Update test_constitution.py and test_editorial.py to match intentional narrow gate.

Do NOT expand regex to compensate for removed rules.
Run tests. Update checklist.
PR title: refactor: narrow constitution to ship backstop
```

---

## Prompt 5 — PR5: Deterministic path dev-only

```
Use the ai-espresso-agent skill. Read context/ai-espresso-pre-launch.md.

Implement Phase 2.3 only:

- Document ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK and mode=deterministic as local dev emergency only.
- Align RANKING_SYSTEM in espresso_agent.py with agent rubric OR add prominent comment that fallback
  is deprecated and must not be enabled in daily-edition.yml.
- Ensure production workflow never sets fallback env.

No behavior change to tool_use loop. Run tests. Update checklist.
PR title: docs: deterministic fallback dev-only
```

---

## Prompt 6 — PR6: Polish + verification edition

```
Use the ai-espresso-agent skill. Read context/ai-espresso-pre-launch.md.

Implement Phase 4 (polish only):

- Footer: Solvd-appropriate contact; correct GitHub repo URL in render_html.py.
- Hide source-tier T1/T2 from public HTML (keep in JSON if needed).
- Compress illustration PNGs on render (reasonable max width/quality).
- Fix README references to missing run_chain.py.
- Optional: tone down or remove Marvel masthead VOICE_CHARACTERS if quick.

Do NOT change agent loop or constitution scope.
Run tests. Update checklist.
PR title: polish: public edition HTML and assets
```

---

## Prompt 7 — Phase 3: Quality verification (after PR1–6 merged)

```
Use the ai-espresso-agent skill. Read context/ai-espresso-pre-launch.md.

Phase 3 only — do not re-implement earlier phases.

- Run agent for a new date with ESPRESSO_SKIP_ARCHIVE=1 and use_cache=True locally (user provides keys).
- Confirm agent_trace: scout → read_candidate/pick → self_critique (revise if needed) → ship_edition.
- Render edition; verify HTML, images, QOTD honesty, no T1 in public view.
- If quality weak, adjust critic/editor prompts only — not new Python regex.

Report: trace summary, headlines chosen, and screenshot description.
Update checklist Phase 3.
```

---

## After each merged PR (optional short prompt)

```
Use the ai-espresso-agent skill. PR [N] merged. Update context/ai-espresso-pre-launch.md checkboxes,
note test count and anything the next session must know. No code changes unless checklist is wrong.
```
