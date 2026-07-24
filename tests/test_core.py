import json
import threading
import tempfile
import unittest
from pathlib import Path

from spanjudge import TraceStore, evaluate_policy, parse_otlp_json


class SpanJudgeTest(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).parents[1] / "demo" / "traces.json"
        self.spans = parse_otlp_json(json.loads(fixture.read_text()))

    def test_parses_otlp_envelope(self):
        self.assertEqual(len(self.spans), 6)
        self.assertEqual(self.spans[1]["attributes"]["gen_ai.tool.name"], "search")

    def test_store_summarizes_agent_traces(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TraceStore(Path(directory) / "test.db")
            self.assertEqual(store.ingest(self.spans), 6)
            summary = store.summary()
            self.assertEqual(summary["trace_count"], 3)
            self.assertEqual(summary["p95_latency_ms"], 980.0)
            self.assertEqual(summary["avg_eval_score"], 0.88)

    def test_policy_fails_latency_regression(self):
        store = TraceStore()
        store.ingest(self.spans)
        result = evaluate_policy(
            store.summary(),
            {"max_p95_latency_ms": 900, "min_avg_eval_score": 0.8},
        )
        self.assertFalse(result["passed"])
        self.assertEqual([check["passed"] for check in result["checks"]], [False, True])

    def test_store_can_be_read_from_server_thread(self):
        store = TraceStore()
        store.ingest(self.spans)
        results = []
        thread = threading.Thread(target=lambda: results.append(store.summary()))
        thread.start()
        thread.join()
        self.assertEqual(results[0]["trace_count"], 3)


if __name__ == "__main__":
    unittest.main()
