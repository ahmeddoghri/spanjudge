from __future__ import annotations

import json
import math
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable


def _attribute_value(value: dict[str, Any]) -> Any:
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in value:
            return value[key]
    if "arrayValue" in value:
        return [_attribute_value(item) for item in value["arrayValue"].get("values", [])]
    return None


def parse_otlp_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the OTLP/HTTP JSON trace envelope into validated span records."""
    spans: list[dict[str, Any]] = []
    for resource in payload.get("resourceSpans", []):
        resource_attrs = {
            item["key"]: _attribute_value(item.get("value", {}))
            for item in resource.get("resource", {}).get("attributes", [])
        }
        for scope in resource.get("scopeSpans", []):
            for raw in scope.get("spans", []):
                trace_id = raw.get("traceId", "")
                span_id = raw.get("spanId", "")
                if not trace_id or not span_id:
                    raise ValueError("every span requires traceId and spanId")
                attributes = dict(resource_attrs)
                attributes.update(
                    {
                        item["key"]: _attribute_value(item.get("value", {}))
                        for item in raw.get("attributes", [])
                    }
                )
                start_ns = int(raw.get("startTimeUnixNano", 0))
                end_ns = int(raw.get("endTimeUnixNano", start_ns))
                if end_ns < start_ns:
                    raise ValueError(f"span {span_id} ends before it starts")
                spans.append(
                    {
                        "trace_id": trace_id,
                        "span_id": span_id,
                        "parent_span_id": raw.get("parentSpanId", ""),
                        "name": raw.get("name", "unnamed"),
                        "start_ns": start_ns,
                        "end_ns": end_ns,
                        "status": raw.get("status", {}).get("code", "STATUS_CODE_UNSET"),
                        "attributes": attributes,
                    }
                )
    if not spans:
        raise ValueError("payload contains no spans")
    return spans


class TraceStore:
    def __init__(self, database: str | Path = ":memory:") -> None:
        self.connection = sqlite3.connect(str(database), check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._migrate_legacy_schema()
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS spans (
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    parent_span_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    start_ns INTEGER NOT NULL,
                    end_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    PRIMARY KEY (trace_id, span_id)
                )
                """
            )
            self.connection.commit()

    def _migrate_legacy_schema(self) -> None:
        """Rebuild a pre-existing `spans` table keyed on span_id alone into
        one keyed on (trace_id, span_id). OTLP only guarantees span_id
        uniqueness within a trace, not globally, so a global span_id
        primary key let two different traces silently overwrite each
        other's row on collision."""
        columns = self.connection.execute("PRAGMA table_info(spans)").fetchall()
        if not columns:
            return
        pk_columns = [row["name"] for row in columns if row["pk"]]
        if pk_columns == ["trace_id", "span_id"] or pk_columns == ["span_id", "trace_id"]:
            return
        self.connection.execute("ALTER TABLE spans RENAME TO spans_legacy")
        self.connection.execute(
            """
            CREATE TABLE spans (
                trace_id TEXT NOT NULL,
                span_id TEXT NOT NULL,
                parent_span_id TEXT NOT NULL,
                name TEXT NOT NULL,
                start_ns INTEGER NOT NULL,
                end_ns INTEGER NOT NULL,
                status TEXT NOT NULL,
                attributes_json TEXT NOT NULL,
                PRIMARY KEY (trace_id, span_id)
            )
            """
        )
        self.connection.execute(
            """
            INSERT OR IGNORE INTO spans
            SELECT trace_id, span_id, parent_span_id, name, start_ns, end_ns, status, attributes_json
            FROM spans_legacy
            """
        )
        self.connection.execute("DROP TABLE spans_legacy")
        self.connection.commit()

    def ingest(self, spans: Iterable[dict[str, Any]]) -> int:
        rows = list(spans)
        with self._lock:
            self.connection.executemany(
                """
                INSERT OR REPLACE INTO spans
                (trace_id, span_id, parent_span_id, name, start_ns, end_ns, status, attributes_json)
                VALUES (:trace_id, :span_id, :parent_span_id, :name, :start_ns, :end_ns, :status, :attributes_json)
                """,
                [
                    {
                        **span,
                        "attributes_json": json.dumps(span["attributes"], sort_keys=True),
                    }
                    for span in rows
                ],
            )
            self.connection.commit()
        return len(rows)

    def traces(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM spans ORDER BY trace_id, start_ns"
            ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["trace_id"], []).append(row)
        traces: list[dict[str, Any]] = []
        for trace_id, items in grouped.items():
            attrs = [json.loads(item["attributes_json"]) for item in items]
            start = min(item["start_ns"] for item in items)
            end = max(item["end_ns"] for item in items)
            tokens_in = sum(int(a.get("gen_ai.usage.input_tokens", 0) or 0) for a in attrs)
            tokens_out = sum(int(a.get("gen_ai.usage.output_tokens", 0) or 0) for a in attrs)
            costs = [float(a.get("spanjudge.cost.usd", 0) or 0) for a in attrs]
            eval_scores = [
                float(a["gen_ai.evaluation.score.value"])
                for a in attrs
                if a.get("gen_ai.evaluation.score.value") is not None
            ]
            traces.append(
                {
                    "trace_id": trace_id,
                    "duration_ms": round((end - start) / 1_000_000, 2),
                    "span_count": len(items),
                    "tool_calls": sum(1 for a in attrs if a.get("gen_ai.tool.name")),
                    "error_spans": sum(
                        1 for item in items if item["status"] == "STATUS_CODE_ERROR"
                    ),
                    "input_tokens": tokens_in,
                    "output_tokens": tokens_out,
                    "cost_usd": round(sum(costs), 6),
                    "eval_score": round(sum(eval_scores) / len(eval_scores), 4)
                    if eval_scores
                    else None,
                }
            )
        return sorted(traces, key=lambda item: item["trace_id"])

    def summary(self) -> dict[str, Any]:
        traces = self.traces()
        if not traces:
            return {
                "trace_count": 0,
                "p95_latency_ms": 0,
                "error_rate": 0,
                "avg_cost_usd": 0,
                "avg_eval_score": None,
            }
        durations = sorted(trace["duration_ms"] for trace in traces)
        p95_index = max(0, math.ceil(len(durations) * 0.95) - 1)
        scores = [trace["eval_score"] for trace in traces if trace["eval_score"] is not None]
        return {
            "trace_count": len(traces),
            "p95_latency_ms": durations[p95_index],
            "error_rate": round(
                sum(1 for trace in traces if trace["error_spans"] > 0) / len(traces), 4
            ),
            "avg_cost_usd": round(
                sum(trace["cost_usd"] for trace in traces) / len(traces), 6
            ),
            "avg_eval_score": round(sum(scores) / len(scores), 4) if scores else None,
            "total_tokens": sum(
                trace["input_tokens"] + trace["output_tokens"] for trace in traces
            ),
        }


def evaluate_policy(summary: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    definitions = [
        ("p95_latency_ms", "max_p95_latency_ms", "<="),
        ("error_rate", "max_error_rate", "<="),
        ("avg_cost_usd", "max_avg_cost_usd", "<="),
        ("avg_eval_score", "min_avg_eval_score", ">="),
    ]
    checks = []
    for metric, policy_key, operator in definitions:
        if policy_key not in policy:
            continue
        actual = summary.get(metric)
        limit = policy[policy_key]
        passed = actual is not None and (actual <= limit if operator == "<=" else actual >= limit)
        checks.append(
            {
                "metric": metric,
                "actual": actual,
                "operator": operator,
                "limit": limit,
                "passed": passed,
            }
        )
    return {"passed": all(check["passed"] for check in checks), "checks": checks}
