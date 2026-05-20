# AI Espresso

AI Espresso generates a daily AI news edition: three curated story cards plus one "Try this prompt" card, rendered for web/email distribution.

## Features

- Multi-step editorial pipeline (Scout -> Editor -> Critic) with tool-based story selection
- Source ingestion from a tiered catalog in `agent/sources.yaml`
- Rendered outputs in both HTML and Markdown
- Edition image generation for card assets
- Publish manifest at `editions/publish/latest.json` for downstream consumers
- Optional email delivery with inline images

## High-Level Architecture

1. `agent/espresso_agent.py` runs the daily selection pipeline and writes edition JSON.
2. `agent/render_edition.py` renders that JSON to HTML/Markdown and image assets.
3. `agent/write_publish_manifest.py` updates a stable pointer to the latest published files.
4. `agent/send_email.py` can send the rendered edition via SMTP.

## Prerequisites

- Python 3.11+
- `pip`
- API keys for model/search/image services, depending on how you run the pipeline

## Quick Start

```bash
cd agent
python3 -m pip install -r requirements.txt
export ANTHROPIC_API_KEY=your_key_here

# Generate an edition JSON for today
python3 espresso_agent.py --use-cache

# Render HTML + Markdown + assets for today
python3 render_edition.py "$(date +%Y-%m-%d)"
```

## Usage

Generate a specific date:

```bash
cd agent
python3 espresso_agent.py --date 2026-05-20 --use-cache
python3 render_edition.py 2026-05-20
```

Preview in a browser:

```bash
cd agent
python3 preview_edition.py 2026-05-20 --render-only
```

Send a dry-run email:

```bash
cd agent
AI_ESPRESSO_FROM=you@example.com \
AI_ESPRESSO_TO=team@example.com \
AI_ESPRESSO_DRY_RUN=1 \
python3 send_email.py ../editions/edition_1_variant_c.html ../editions/edition_1_variant_c.md
```

## Outputs

- `agent/data/editions/YYYY-MM-DD.json` - raw edition payload and trace
- `editions/edition_N_variant_c.html` - rendered HTML edition
- `editions/edition_N_variant_c.md` - rendered Markdown edition
- `editions/edition_N/assets/variant_c_*.png` - card images
- `editions/publish/latest.json` - machine-readable latest-edition manifest

## Automation

- `/.github/workflows/daily-edition.yml` runs daily at 7:00 AM ET.
- `/.github/workflows/tests.yml` runs unit tests on pushes and pull requests that touch `agent/**`.

## Testing

```bash
cd agent
python3 -m unittest discover -s tests -p "test_*.py" -v
```

## Repository Structure

```text
ai-espresso-finalized/
├── agent/                      # generation, rendering, email, and tests
├── editions/                   # published HTML/MD/assets and latest manifest
├── .github/workflows/          # scheduled edition + CI test workflows
└── README.md
```

## Contributing

- Keep changes scoped and testable.
- Run the unit test suite before opening a PR.
- If you change output contracts, verify `editions/publish/latest.json` still reflects rendered artifacts.
