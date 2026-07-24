"""OpenTelemetry-shaped trace storage and regression gates for AI agents."""

from .core import TraceStore, evaluate_policy, parse_otlp_json

__all__ = ["TraceStore", "evaluate_policy", "parse_otlp_json"]
__version__ = "0.1.0"
