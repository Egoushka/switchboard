"""Prometheus series. The chars pair is how S2's savings are measured, not estimated."""

from prometheus_client import Counter, Gauge, Histogram

calls = Counter("switchboard_calls_total", "Upstream tool calls", ["scope", "server", "verb", "outcome"])
result_chars = Counter("switchboard_result_chars_total", "Result chars before and after shaping", ["scope", "server", "stage"])
approvals = Counter("switchboard_approvals_total", "Write approval decisions", ["scope", "decision"])
approval_wait = Histogram(
    "switchboard_approval_wait_seconds", "From asking to a decision", buckets=(5, 10, 20, 30, 45, 60, 90, 120, 180)
)
catalog_tools = Gauge("switchboard_catalog_tools", "Tools in a scope's catalog", ["scope"])
upstream_errors = Counter("switchboard_upstream_errors_total", "Upstream failures", ["scope"])
