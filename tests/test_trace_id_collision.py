import sqlite3
import tempfile
import unittest
from pathlib import Path

from spanjudge import TraceStore, parse_otlp_json


def _payload(trace_id, span_id, name, start_ns, end_ns):
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "parentSpanId": "",
                                "name": name,
                                "startTimeUnixNano": str(start_ns),
                                "endTimeUnixNano": str(end_ns),
                                "status": {"code": "STATUS_CODE_OK"},
                                "attributes": [],
                            }
                        ]
                    }
                ],
            }
        ]
    }


class TraceIdCollisionTest(unittest.TestCase):
    """OTLP only guarantees span_id uniqueness within a single trace, not
    globally (https://opentelemetry.io/docs/specs/otel/trace/api/#spancontext).
    TraceStore's original schema used span_id alone as the SQLite primary
    key, so two different traces that happened to reuse the same span_id
    silently overwrote each other -- one trace's data vanished from the
    store entirely, with no error raised anywhere in the ingest path."""

    def test_two_traces_sharing_a_span_id_are_both_preserved(self):
        store = TraceStore()
        trace_a = parse_otlp_json(_payload("trace-A" * 4, "collide0123456789", "step-A", 0, 1_000_000_000))
        trace_b = parse_otlp_json(_payload("trace-B" * 4, "collide0123456789", "step-B", 5_000_000_000, 6_000_000_000))

        store.ingest(trace_a)
        store.ingest(trace_b)

        traces = store.traces()
        trace_ids = {t["trace_id"] for t in traces}
        self.assertEqual(len(traces), 2)
        self.assertIn("trace-A" * 4, trace_ids)
        self.assertIn("trace-B" * 4, trace_ids)

    def test_same_trace_reingesting_the_same_span_still_replaces_it(self):
        """The fix must not break legitimate re-ingestion of the same span
        within the same trace (e.g. a client retry sending updated status)."""
        store = TraceStore()
        first = parse_otlp_json(_payload("trace-X" * 4, "span-1", "step", 0, 1_000_000_000))
        first[0]["status"] = "STATUS_CODE_ERROR"
        store.ingest(first)

        second = parse_otlp_json(_payload("trace-X" * 4, "span-1", "step", 0, 1_000_000_000))
        second[0]["status"] = "STATUS_CODE_OK"
        store.ingest(second)

        traces = store.traces()
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0]["error_spans"], 0)

    def test_legacy_database_with_span_id_only_primary_key_is_migrated_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            conn = sqlite3.connect(str(path))
            conn.execute(
                """
                CREATE TABLE spans (
                    trace_id TEXT NOT NULL,
                    span_id TEXT PRIMARY KEY,
                    parent_span_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    start_ns INTEGER NOT NULL,
                    end_ns INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    attributes_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO spans VALUES ('trace-old','span-old','','legacy-span',0,100,'STATUS_CODE_OK','{}')"
            )
            conn.commit()
            conn.close()

            store = TraceStore(path)
            columns = store.connection.execute("PRAGMA table_info(spans)").fetchall()
            pk_columns = [c["name"] for c in columns if c["pk"]]
            self.assertEqual(pk_columns, ["trace_id", "span_id"])
            self.assertEqual(len(store.traces()), 1)
            self.assertEqual(store.traces()[0]["trace_id"], "trace-old")

            # migration should be idempotent -- reopening doesn't re-migrate or lose data
            store2 = TraceStore(path)
            self.assertEqual(len(store2.traces()), 1)


if __name__ == "__main__":
    unittest.main()
