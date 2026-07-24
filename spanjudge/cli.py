from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core import TraceStore, evaluate_policy, parse_otlp_json
from .server import serve


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="spanjudge")
    parser.add_argument("--database", default="spanjudge.db")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="ingest an OTLP/HTTP JSON trace file")
    ingest.add_argument("path")
    commands.add_parser("summary", help="print aggregate trace metrics")
    evaluate = commands.add_parser("evaluate", help="apply a JSON regression policy")
    evaluate.add_argument("policy")
    server = commands.add_parser("serve", help="run the collector API and dashboard")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=4318)
    args = parser.parse_args(argv)

    if args.command == "serve":
        serve(args.database, args.host, args.port)
        return 0
    store = TraceStore(args.database)
    if args.command == "ingest":
        payload = json.loads(Path(args.path).read_text())
        print(json.dumps({"accepted_spans": store.ingest(parse_otlp_json(payload))}, indent=2))
        return 0
    summary = store.summary()
    if args.command == "summary":
        print(json.dumps(summary, indent=2))
        return 0
    policy = json.loads(Path(args.policy).read_text())
    result = evaluate_policy(summary, policy)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1
