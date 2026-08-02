# spanjudge

**An OTLP trace collector, dashboard, and release gate for AI agents.**

Agent tests usually grade the final answer and miss the path that produced it. `spanjudge` accepts OpenTelemetry Protocol JSON traces, stores every agent and tool span in SQLite, computes latency/cost/error/evaluation metrics, and turns those metrics into a CI exit code.

![spanjudge dashboard](demo/dashboard.png)

## What ships

- OTLP/HTTP JSON ingestion at `POST /v1/traces`
- OpenTelemetry GenAI attributes for agent, tool, token, and evaluation telemetry
- SQLite trace storage with no external services
- Responsive browser dashboard
- JSON regression policies usable from CI
- CLI, HTTP API, Docker image, tests, and GitHub Actions matrix

## Run it end to end

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e .
spanjudge --database demo.db ingest demo/traces.json
spanjudge --database demo.db evaluate demo/policy.json
spanjudge --database demo.db serve
```

Open <http://127.0.0.1:4318>. The policy command exits non-zero when a release violates latency, error-rate, cost, or evaluation-score limits.

Docker is one command:

```bash
docker compose up --build
```

## API

```bash
curl -X POST http://127.0.0.1:4318/v1/traces \
  -H 'content-type: application/json' \
  --data-binary @demo/traces.json

curl http://127.0.0.1:4318/api/summary
curl http://127.0.0.1:4318/api/traces
curl -X POST http://127.0.0.1:4318/api/evaluate \
  -H 'content-type: application/json' \
  --data-binary @demo/policy.json
```

## Why this is current

OpenTelemetry's GenAI semantic conventions now define agent invocation, tool execution, token usage, latency, and evaluation telemetry. `spanjudge` implements the useful storage and release-gating layer around those attributes without pretending the still-developmental conventions are stable.

- [OpenTelemetry GenAI attribute registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
- [OpenTelemetry GenAI semantic-conventions repository](https://github.com/open-telemetry/semantic-conventions/tree/main/docs/gen-ai)

## Update: fixed a cross-trace span_id collision that silently dropped traces

`TraceStore`'s SQLite schema used `span_id` alone as the primary key. The
OTLP spec only guarantees span_id uniqueness *within* a trace, not
globally — so two different traces that happened to reuse the same
span_id would silently overwrite each other's row on ingest, with no
error anywhere in the path. One trace's spans, cost, latency, and
evaluation data would simply vanish from the store. In a release-gating
tool, that means `evaluate` could pass a policy check against incomplete
or wrong data without any indication something was dropped.

Fixed by keying the table on `(trace_id, span_id)` instead, matching what
the spec actually guarantees. A startup migration detects and safely
upgrades any pre-existing database still using the old single-column
primary key, preserving existing rows in place. `tests/test_trace_id_collision.py`
covers the original collision, the fix, that legitimate same-trace span
re-ingestion (e.g. a client retry) still works, and that the legacy-schema
migration is correct and idempotent.

## Scope

This is an OTLP/HTTP JSON trace receiver and evaluation workbench, not a full OpenTelemetry Collector distribution. Content-bearing attributes may contain sensitive data; production deployments should apply redaction, authentication, retention, and transport security before ingestion.

## Test

```bash
python -m unittest discover -s tests -v
```

MIT licensed.
