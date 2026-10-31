from dataclasses import dataclass


@dataclass(frozen=True)
class Summary:
    completed: int
    throughput_qps: float
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None


def percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * pct / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(latencies_ms, window_s):
    return Summary(
        completed=len(latencies_ms),
        throughput_qps=len(latencies_ms) / window_s,
        p50_ms=percentile(latencies_ms, 50),
        p95_ms=percentile(latencies_ms, 95),
        p99_ms=percentile(latencies_ms, 99),
    )
