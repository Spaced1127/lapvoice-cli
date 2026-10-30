from iracing_coach.demo import make_lap
from iracing_coach.telemetry import LapRecorder


def feed_lap(rec, lap, **kw):
    out = None
    for s in lap.samples:
        got = rec.feed(s.dist_pct, s.session_time, s.speed, s.throttle, s.brake,
                       last_lap_time=lap.lap_time, lap_counter=3, **kw)
        out = got or out
    return out


def test_completes_a_lap_on_rollover():
    rec = LapRecorder()
    feed_lap(rec, make_lap())
    done = feed_lap(rec, make_lap(t0=2000.0))
    assert done and done.complete and not done.pit and not done.offtrack
    assert abs(done.lap_time - make_lap().lap_time) < 0.05 and done.lap_num == 2


def test_pit_and_offtrack_flags():
    rec = LapRecorder()
    feed_lap(rec, make_lap(), on_pit=True)
    assert feed_lap(rec, make_lap(t0=2000.0), off_track=True).pit
    rec2 = LapRecorder()
    feed_lap(rec2, make_lap(), off_track=True)
    assert feed_lap(rec2, make_lap(t0=2000.0)).offtrack


def test_teleport_discards_partial_lap():
    rec = LapRecorder()
    lap = make_lap()
    for s in lap.samples[: len(lap.samples) // 2]:
        rec.feed(s.dist_pct, s.session_time, s.speed, s.throttle, s.brake)
    rec.feed(0.05, 5000.0, 10, 0, 0)       # jumped back: reset to pits
    assert len(rec.samples) == 1


def test_partial_first_lap_is_not_complete():
    rec = LapRecorder()
    lap = make_lap()
    for s in lap.samples[len(lap.samples) // 2:]:      # joined mid-lap
        rec.feed(s.dist_pct, s.session_time, s.speed, s.throttle, s.brake, last_lap_time=40, lap_counter=2)
    done = feed_lap(rec, make_lap(t0=3000.0))
    assert done is not None and done.complete is False
