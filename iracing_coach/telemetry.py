"""Live telemetry from iRacing via the official SDK (read-only).

Windows only, and only while iRacing is running with the car on track.
`irsdk` is imported lazily so the rest of the package (analysis, tests,
demo mode) works on any OS.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(slots=True)
class LapSample:
    dist_pct: float      # 0.0 - 1.0 position around the lap
    session_time: float  # seconds
    speed: float         # m/s
    throttle: float      # 0 - 1
    brake: float         # 0 - 1


@dataclass(slots=True)
class CompletedLap:
    lap_num: int
    samples: list
    lap_time: float
    pit: bool = False       # touched pit road (in/out lap)
    offtrack: bool = False  # car centre went off the racing surface
    complete: bool = True   # samples cover (almost) the whole lap


OFF_TRACK_SURFACE = 0  # irsdk.TrkLoc.off_track


class LapRecorder:
    """Turns a stream of telemetry samples into CompletedLap objects.

    Pure logic (no iRacing needed), shared by the CLI coach and the Pro app.
    Handles lap rollover, pit visits, off-track laps, and resets/teleports.
    """

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.samples: list = []
        self.pit = False
        self.offtrack = False
        self.last_dist: Optional[float] = None
        self.lap_start_time: Optional[float] = None

    def feed(self, dist, session_time, speed, throttle, brake, on_pit=False,
             off_track=False, last_lap_time=None, lap_counter=1) -> Optional[CompletedLap]:
        last = self.last_dist
        wrapped = last is not None and last > 0.9 and dist < 0.1
        if last is not None and not wrapped and dist < last - 0.2:
            self.reset()  # reset to pits / teleport: discard the partial lap

        completed = self._finish(last_lap_time, lap_counter) if wrapped else None

        if on_pit:
            self.pit = True
        if off_track:
            self.offtrack = True
        if not self.samples:
            self.lap_start_time = session_time
        self.samples.append(LapSample(dist, session_time, speed or 0.0,
                                      throttle or 0.0, brake or 0.0))
        self.last_dist = dist
        return completed

    def _finish(self, last_lap_time, lap_counter) -> Optional[CompletedLap]:
        samples, pit, off = self.samples, self.pit, self.offtrack
        self.samples, self.pit, self.offtrack = [], False, False
        self.lap_start_time = None
        if len(samples) < 30:
            return None
        lap_time = last_lap_time if last_lap_time and last_lap_time > 0 else (
            samples[-1].session_time - samples[0].session_time)
        complete = samples[0].dist_pct < 0.05 and samples[-1].dist_pct > 0.95
        return CompletedLap(
            lap_num=max(int(lap_counter or 1) - 1, 1),
            samples=samples, lap_time=lap_time,
            pit=pit, offtrack=off, complete=complete,
        )


class IRacingTelemetry:
    def __init__(self, poll_hz: int = 60,
                 on_session_change: Optional[Callable[[tuple], None]] = None):
        try:
            import irsdk
        except ImportError as e:  # pragma: no cover - environment dependent
            raise ImportError(
                "The 'pyirsdk' package is not installed (it only works on Windows). "
                "Run setup.bat, or: pip install pyirsdk"
            ) from e
        self.ir = irsdk.IRSDK()
        self.poll_interval = 1.0 / poll_hz
        self.on_session_change = on_session_change
        self._session_key = None
        self._last_key_check = 0.0
        self.recorder = LapRecorder()

    # ---- connection -------------------------------------------------
    def _is_up(self) -> bool:
        return bool(self.ir.startup() and self.ir.is_initialized and self.ir.is_connected)

    def connect(self) -> None:
        print("Waiting for iRacing (start it and get in the car)...")
        while not self._is_up():
            time.sleep(2)
        print("Connected to iRacing.")

    def _ensure_connected(self) -> None:
        if not (self.ir.is_initialized and self.ir.is_connected):
            print("Lost connection to iRacing. Waiting to reconnect...")
            self.ir.shutdown()
            while not self._is_up():
                time.sleep(2)
            print("Reconnected.")

    def shutdown(self) -> None:
        try:
            self.ir.shutdown()
        except Exception:
            pass

    # ---- helpers ----------------------------------------------------
    def _read_session_key(self):
        """(track id, car id) - when this changes, old baselines are meaningless."""
        try:
            track = self.ir["WeekendInfo"]["TrackID"]
            drivers = self.ir["DriverInfo"]
            car = drivers["Drivers"][drivers["DriverCarIdx"]]["CarID"]
            return (track, car)
        except Exception:
            return None

    def _check_session(self) -> None:
        now = time.time()
        if now - self._last_key_check < 2.0:
            return
        self._last_key_check = now
        key = self._read_session_key()
        if key is None:
            return
        if self._session_key is not None and key != self._session_key:
            self.recorder.reset()
            if self.on_session_change:
                self.on_session_change(key)
        self._session_key = key

    # ---- main loop --------------------------------------------------
    def poll_loop(self, on_lap_complete: Callable[[CompletedLap], None]) -> None:
        self.connect()
        ir = self.ir

        while True:
            self._ensure_connected()
            ir.freeze_var_buffer_latest()
            self._check_session()

            dist = ir["LapDistPct"]
            if not ir["IsOnTrack"] or dist is None or dist < 0:
                self.recorder.reset()
                time.sleep(self.poll_interval)
                continue

            lap = self.recorder.feed(
                dist, ir["SessionTime"], ir["Speed"], ir["Throttle"], ir["Brake"],
                on_pit=bool(ir["OnPitRoad"]),
                off_track=ir["PlayerTrackSurface"] == OFF_TRACK_SURFACE,
                last_lap_time=ir["LapLastLapTime"], lap_counter=ir["Lap"])
            if lap:
                on_lap_complete(lap)
            time.sleep(self.poll_interval)
