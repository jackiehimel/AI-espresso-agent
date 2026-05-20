# AI Espresso

Shippable package for Solvd AI Garage integration (agent + `editions/` publish surface).

A Python agent that produces a daily AI news digest. Each run:

1. Pulls candidate stories from ~45 sources (RSS, blogs, primary press) defined in `agent/sources.yaml`.
2. Runs a three-role LLM loop (Scout, Editor, Critic) over those candidates to select three stories plus one prompt-of-the-day.
3. Renders the result to HTML and Markdown and generates four illustrations (one per card).
4. Optionally emails the rendered HTML inline via SMTP.

Output is written to `editions/` (rendered files) and `agent/data/editions/` (raw JSON + agent trace).

## Recent milestones (May 2026)

- Strengthened story-selection guardrails so weak-pool and duplicate-story paths recover safely.
- Hardened archive, render, and source-ingestion reliability for CI and production edition runs.
- Improved production email UX with hosted prompt form support and fully clickable story cards.
- Completed pre-launch launch-gate documentation and rollback/runbook notes for operations.

## Quick start

```bash
cd agent
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...

# 1. Generate today's edition JSON
python3 -c "import datetime as dt, espresso_agent; \
  espresso_agent.run(dt.date.today(), dry_run=False, use_cache=True, mode='agent')"

# 2. Render to HTML + Markdown + illustrations (optional: --no-images)
python3 render_edition.py $(date +%Y-%m-%d)
```

## Tests

```bash
cd agent
python3 -m unittest discover -s tests -p "test_*.py" -v
```

## Automation

[`.github/workflows/daily-edition.yml`](.github/workflows/daily-edition.yml) runs daily at `0 11 * * *` UTC (7am ET during EDT, 6am ET during EST — GitHub Actions does not observe DST). Steps: generate → render → email → commit.

## Docs

Full architecture, agent loop, output schema, secrets, and downstream sync contract: [agent/README.md](agent/README.md).
