"""Lap comparison.

Splits each lap into equal-distance segments, works out how long each segment
took (interpolating the exact time at every segment boundary, so the result
doesn't depend on the polling rate), then reports the regions where the
current lap lost the most time against the best lap.

Pure Python, no iRacing needed - fully unit-testable.
"""

from __future__ import annotations

from bisect import bisect_left
from statistics import mean, pstdev

BRAKE_THRESHOLD = 0.15       # brake pedal fraction that counts as "braking"
BRAKE_DIFF_PCT = 0.004       # 0.4% of a lap - smaller shifts are ignored
THROTTLE_DIFF = 0.12         # average throttle gap worth mentioning
SEGMENT_NOISE_FLOOR = 0.01   # seconds; per-segment losses below this are noise
MIN_SAMPLES = 30
MAX_REGION_FRACTION = 0.2    # a single finding never spans more than this share of the lap
BRAKE_LOOKBACK_SEGMENTS = 2  # segments before a loss region that count as its braking zone


def _monotonic(values):
    out, hi = [], -1.0
    for v in values:
        hi = v if v > hi else hi
        out.append(hi)
    return out


def _interp(xs, ys, target):
    if target <= xs[0]:
        return ys[0]
    if target >= xs[-1]:
        return ys[-1]
    j = bisect_left(xs, target)
    x0, x1 = xs[j - 1], xs[j]
    if x1 == x0:
        return ys[j]
    return ys[j - 1] + (target - x0) / (x1 - x0) * (ys[j] - ys[j - 1])


def segment_stats(samples, n):
    """Per-segment duration, minimum speed, average throttle, brake point."""
    if len(samples) < MIN_SAMPLES:
        return None
    pcts = _monotonic([s.dist_pct for s in samples])
    times = [s.session_time for s in samples]
    bounds = [_interp(pcts, times, i / n) for i in range(n + 1)]

    buckets = [[] for _ in range(n)]
    for s, p in zip(samples, pcts):
        buckets[min(int(p * n), n - 1)].append(s)

    out = []
    for i, seg in enumerate(buckets):
        entry = {"duration": bounds[i + 1] - bounds[i],
                 "min_speed": None, "avg_throttle": None, "brake_pct": None}
        if seg:
            entry["min_speed"] = min(s.speed for s in seg)
            entry["avg_throttle"] = mean(s.throttle for s in seg)
            entry["brake_pct"] = next(
                (s.dist_pct for s in seg if s.brake > BRAKE_THRESHOLD), None)
        out.append(entry)
    return out


def _split_run(deltas, i, j, max_len):
    """Break a long run of slow segments into its worst hot-spots (each <= max_len segments).

    A uniformly slow lap (traffic, a cold-tyre lap) would otherwise be reported as one
    useless 'you lost time between 0 and 100 percent' finding."""
    if j - i + 1 <= max_len:
        return [(i, j)]
    free, out = set(range(i, j + 1)), []
    while True:
        best = None
        for a in range(i, j - max_len + 2):
            if all(k in free for k in range(a, a + max_len)):
                v = sum(deltas[a:a + max_len])
                if best is None or v > best[0]:
                    best = (v, a)
        if best is None:
            return out
        a = best[1]
        out.append((a, a + max_len - 1))
        free -= set(range(a, a + max_len))


def compare_to_best(current, best, num_segments, top_n, min_time_loss=0.04):
    """Return up to top_n findings (biggest time loss first).

    Each finding is a contiguous region of the lap where the current lap was
    slower than the best lap. Speeds are m/s (negative delta = slower).
    """
    c = segment_stats(current, num_segments)
    b = segment_stats(best, num_segments)
    if c is None or b is None:
        return []

    n = num_segments
    deltas = [c[i]["duration"] - b[i]["duration"] for i in range(n)]

    regions, i = [], 0
    while i < n:
        if deltas[i] > SEGMENT_NOISE_FLOOR:
            j = i
            while j + 1 < n and deltas[j + 1] > SEGMENT_NOISE_FLOOR:
                j += 1
            regions.extend(_split_run(deltas, i, j, max(2, int(n * MAX_REGION_FRACTION))))
            i = j + 1
        else:
            i += 1

    findings = []
    for i, j in regions:
        loss = sum(deltas[i:j + 1])
        if loss < min_time_loss:
            continue
        cs, bs = c[i:j + 1], b[i:j + 1]

        c_min = [s["min_speed"] for s in cs if s["min_speed"] is not None]
        b_min = [s["min_speed"] for s in bs if s["min_speed"] is not None]
        speed_delta = (min(c_min) - min(b_min)) if c_min and b_min else None

        # Braking happens just BEFORE the time is lost, so look back a little.
        lo = max(0, i - BRAKE_LOOKBACK_SEGMENTS)
        c_brk = [s["brake_pct"] for s in c[lo:j + 1] if s["brake_pct"] is not None]
        b_brk = [s["brake_pct"] for s in b[lo:j + 1] if s["brake_pct"] is not None]
        brake_note = None
        if c_brk and b_brk:
            diff = min(c_brk) - min(b_brk)
            if diff < -BRAKE_DIFF_PCT:
                brake_note = "braked earlier than on your best lap"
            elif diff > BRAKE_DIFF_PCT:
                brake_note = "braked later than on your best lap"
        elif c_brk:
            brake_note = "braked here but not on your best lap"
        elif b_brk:
            brake_note = "did not brake here but did on your best lap"

        c_thr = [s["avg_throttle"] for s in cs if s["avg_throttle"] is not None]
        b_thr = [s["avg_throttle"] for s in bs if s["avg_throttle"] is not None]
        throttle_note = None
        if c_thr and b_thr and mean(c_thr) - mean(b_thr) < -THROTTLE_DIFF:
            throttle_note = "less throttle than on your best lap"

        findings.append({
            "start_pct": int(round(i / n * 100)),
            "end_pct": int(round((j + 1) / n * 100)),
            "time_lost": round(loss, 2),
            "min_speed_delta": speed_delta,
            "brake_note": brake_note,
            "throttle_note": throttle_note,
        })

    findings.sort(key=lambda f: f["time_lost"], reverse=True)
    return findings[:top_n]


def summarize_session(lap_times):
    valid = [t for t in lap_times if t]
    if not valid:
        return None
    return {
        "laps": len(valid),
        "best": min(valid),
        "worst": max(valid),
        "avg": sum(valid) / len(valid),
        "spread": pstdev(valid) if len(valid) > 1 else 0.0,
    }
