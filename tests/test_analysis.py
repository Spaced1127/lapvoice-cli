from iracing_coach.analysis import compare_to_best, segment_stats, summarize_session
from iracing_coach.demo import make_lap


def test_segment_durations_sum_to_lap_time():
    lap = make_lap()
    segs = segment_stats(lap.samples, 20)
    assert abs(sum(s["duration"] for s in segs) - lap.lap_time) < 0.05


def test_identical_laps_with_different_sampling_phase_have_no_findings():
    # Same physics, sampled at a different phase: interpolation must cancel the jitter.
    a, b = make_lap(phase=0.0), make_lap(phase=0.5)
    assert compare_to_best(b.samples, a.samples, 20, 3, 0.04) == []


def test_finds_the_slow_corner():
    best = make_lap()
    slow = make_lap(slow_corner=1, brake_shift=0.012, phase=0.3)
    findings = compare_to_best(slow.samples, best.samples, 20, 3, 0.04)
    assert findings, "expected a finding for the slow corner"
    top = findings[0]
    assert 35 <= top["start_pct"] <= 55 and 45 <= top["end_pct"] <= 65
    assert top["time_lost"] > 0.04
    assert top["min_speed_delta"] < 0
    assert top["brake_note"] == "braked earlier than on your best lap"


def test_top_n_limits_results():
    best = make_lap()
    slow = make_lap(slow_corner=2, brake_shift=0.012)
    assert len(compare_to_best(slow.samples, best.samples, 20, 1, 0.01)) <= 1


def test_too_few_samples_returns_empty():
    best = make_lap()
    assert compare_to_best(best.samples[:5], best.samples, 20, 2) == []


def test_summarize_session():
    s = summarize_session([60.0, 61.0, 59.5])
    assert s["laps"] == 3 and s["best"] == 59.5 and s["worst"] == 61.0
    assert summarize_session([]) is None


def test_uniformly_slow_lap_is_split_into_hotspots_not_one_giant_region():
    # The whole lap is slower (traffic / cold tyres), so every segment loses time.
    from iracing_coach.analysis import MAX_REGION_FRACTION
    best = make_lap()
    slow = make_lap(slow_corner=1, brake_shift=0.012)
    for s in slow.samples:                     # stretch time: everything 4% slower
        s.session_time = 1000.0 + (s.session_time - 1000.0) * 1.04
    findings = compare_to_best(slow.samples, best.samples, 20, 3, 0.04)
    assert findings
    for f in findings:
        assert (f["end_pct"] - f["start_pct"]) <= MAX_REGION_FRACTION * 100 + 1
    assert not any(f["start_pct"] == 0 and f["end_pct"] == 100 for f in findings)
