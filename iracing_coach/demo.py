"""Synthetic laps for --demo mode and the test-suite (no iRacing needed)."""

from __future__ import annotations

import math

from .telemetry import CompletedLap, LapSample

CORNERS = (0.22, 0.55, 0.83)   # corner apex positions (fraction of lap)
V_MAX, V_CORNER, TRACK_LEN = 62.0, 24.0, 1800.0


def _corner_speed(k, slow_corner):
    return V_CORNER - 3.0 if k == slow_corner else V_CORNER


def make_lap(slow_corner=None, brake_shift=0.0, hz=60, phase=0.0, t0=1000.0, lap_num=1):
    """One synthetic lap. `slow_corner` (0-2) carries less speed and brakes
    `brake_shift` (fraction of a lap) earlier - a deliberate mistake to find."""
    def speed(p):
        v = V_MAX
        for k, c in enumerate(CORNERS):
            v -= (V_MAX - _corner_speed(k, slow_corner)) * math.exp(-((p - c) / 0.025) ** 2)
        return v

    dt = 1.0 / hz
    p = phase * speed(0.0) * dt / TRACK_LEN
    t = t0 + phase * dt
    samples = []
    while p < 1.0:
        brake, throttle = 0.0, 1.0
        for k, c in enumerate(CORNERS):
            shift = brake_shift if k == slow_corner else 0.0
            if c - 0.07 - shift <= p < c - 0.02:
                brake, throttle = 0.85, 0.0
            elif c - 0.02 <= p < c + 0.015:
                throttle = 0.4
        samples.append(LapSample(p, t, speed(p), throttle, brake))
        p += speed(p) * dt / TRACK_LEN
        t += dt
    return CompletedLap(lap_num=lap_num, samples=samples, lap_time=t - t0)
