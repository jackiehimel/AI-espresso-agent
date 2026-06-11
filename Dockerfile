FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

COPY agent/requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ agent/
COPY editions/ editions/

WORKDIR /app/agent

ENTRYPOINT ["python", "-c", "\
import datetime as dt, espresso_agent;\
espresso_agent.run(dt.date.today(), dry_run=False, use_cache=False, mode='agent')\
"]
