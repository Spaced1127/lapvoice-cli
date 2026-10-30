"""Command-line entry point and session logic."""

from __future__ import annotations

import argparse
import platform
import threading
import time

from . import __version__
from .analysis import compare_to_best, summarize_session
from .coach import LocalCoach, fmt_time, spoken_time
from .config import ConfigError, load_config
from .voice import Voice


class CoachSession:
    def __init__(self, cfg, coach, voice, on_tip=None):
        self.cfg, self.coach, self.voice = cfg, coach, voice
        self.on_tip = on_tip  # optional callback(text, lap_num), used by the Pro overlays
        self.best_samples = None
        self.best_time = None
        self.lap_times: list = []
        self._laps_since_tip = 0
        self._lock = threading.Lock()
        self._generating = False

    def reset(self):
        self.best_samples = self.best_time = None
        self.lap_times = []
        self._laps_since_tip = 0

    def on_session_change(self, key):
        print("\nTrack or car changed - starting a fresh baseline.")
        self.reset()

    def on_lap_complete(self, lap, wait=False):
        a, b = self.cfg["analysis"], self.cfg["behavior"]
        t = lap.lap_time

        if lap.pit or not lap.complete:
            print(f"Lap {lap.lap_num}: skipped (in/out lap or partial lap)")
            return
        if not a["min_valid_lap_seconds"] <= t <= a["max_valid_lap_seconds"]:
            print(f"Lap {lap.lap_num}: {fmt_time(t)} - ignored (outside valid lap-time range)")
            return
        if lap.offtrack and a["ignore_offtrack_laps"]:
            print(f"Lap {lap.lap_num}: {fmt_time(t)} - went off track, not analysed")
            return

        print(f"Lap {lap.lap_num} complete: {fmt_time(t)}")
        self.lap_times.append(t)

        if self.best_samples is None:
            self.best_samples, self.best_time = lap.samples, t
            print(f"  -> baseline lap set ({fmt_time(t)})")
            if b["speak_baseline_message"]:
                self.voice.say("Baseline lap set. Tips start from the next lap.")
            return

        # Analyse against the PREVIOUS best before we replace it.
        findings = compare_to_best(
            lap.samples, self.best_samples, a["num_segments"],
            a["top_findings_to_report"], a["min_time_loss_seconds"])
        delta = t - self.best_time

        if t < self.best_time:
            print(f"  -> new best lap! {fmt_time(t)} (was {fmt_time(self.best_time)})")
            self.best_samples, self.best_time = lap.samples, t
            if b["announce_new_best"]:
                self.voice.say(f"New best lap. {spoken_time(t)}")

        self._laps_since_tip += 1
        if self._laps_since_tip < b["tip_every_n_laps"]:
            return
        if not findings:
            print("  (nothing significant to flag on this lap)")
            return

        with self._lock:
            if self._generating:
                print(f"  (skipping tip for lap {lap.lap_num} - still working on the previous one)")
                return
            self._generating = True
        self._laps_since_tip = 0

        worker = threading.Thread(target=self._tip_worker,
                                  args=(lap.lap_num, t, delta, findings), daemon=True)
        worker.start()
        if wait:
            worker.join()

    def _tip_worker(self, lap_num, lap_time, delta, findings):
        try:
            started = time.time()
            tip = self.coach.lap_tip(lap_num, lap_time, delta, findings)
            if tip:
                print(f"  Coach: {tip}   ({time.time() - started:.1f}s)")
                self.voice.say(tip)
                if self.on_tip:
                    self.on_tip(tip, lap_num)
        finally:
            with self._lock:
                self._generating = False

    def finish(self, speak=None):
        """Print (and optionally speak) the session summary. Returns the summary text."""
        stats = summarize_session(self.lap_times)
        if not stats:
            print("No valid laps recorded this session.")
            return None
        print("\n--- Session summary ---")
        print(f"Laps: {stats['laps']}   Best: {fmt_time(stats['best'])}   "
              f"Avg: {fmt_time(stats['avg'])}   Worst: {fmt_time(stats['worst'])}")
        if speak is None:
            speak = self.cfg["behavior"]["speak_session_summary_on_exit"]
        summary = None
        if speak:
            summary = self.coach.session_summary(stats)
            if summary:
                print(f"Coach: {summary}")
                self.voice.say(summary, max_age_sec=60)
                self.voice.wait_idle(30)
        return summary


# ------------------------------------------------------------------ commands
def build_parts(cfg):
    o, c = cfg["ollama"], cfg["coach"]
    coach = LocalCoach(
        host=o["host"], model=o["model"], temperature=o["temperature"],
        max_tokens=o["max_tokens"], num_ctx=o["num_ctx"], keep_alive=o["keep_alive"],
        timeout=o["timeout_seconds"], units=c["units"],
        use_llm=c["use_llm"], fallback_to_template=c["fallback_to_template"])
    return coach, Voice(**cfg["voice"])


def run_check(cfg, used, coach, voice):
    def line(ok, msg):
        print(f"  [{'OK' if ok else '!!' if ok is False else '--'}] {msg}")

    print(f"LapVoice v{__version__} - environment check\n")
    line(True, f"Python {platform.python_version()} on {platform.system()}")
    line(True if used else None,
         "Config: " + (", ".join(p.name for p in used) if used else "no config file found, using defaults"))

    try:
        import irsdk
        line(True, "pyirsdk is installed")
        ir = irsdk.IRSDK()
        up = bool(ir.startup() and ir.is_initialized and ir.is_connected)
        line(True if up else None,
             "iRacing is running and connected" if up else
             "iRacing isn't running right now (fine for this check)")
        ir.shutdown()
    except Exception as e:
        line(False, f"pyirsdk unavailable: {e}  (Windows only; run setup.bat)")

    if cfg["coach"]["use_llm"]:
        ok, msg = coach.check()
        line(ok, msg)
        if ok:
            t0 = time.time()
            coach.warm_up()
            load = time.time() - t0
            t1 = time.time()
            tip = coach.lap_tip(1, 60.0, 0.2, [{
                "start_pct": 30, "end_pct": 38, "time_lost": 0.2,
                "min_speed_delta": -2.0, "brake_note": "braked earlier than on your best lap",
                "throttle_note": None}])
            reply = time.time() - t1
            line(bool(tip), f"Model load {load:.1f}s, sample tip in {reply:.1f}s: {tip}")
            if reply > 4:
                print("       Slow replies. Try a smaller model (see README), and run "
                      "`ollama ps` to confirm it's on the GPU.")
    else:
        line(None, "AI model disabled (coach.use_llm: false) - using built-in tips")

    if voice.enabled:
        voice.start()
        voice.say("Voice check. If you can hear this, speech is working.")
        voice.wait_idle(10)
        line(voice.enabled, "Text-to-speech test spoken (did you hear it?)")
    else:
        line(None, "Voice disabled in config")
    return 0


def run_demo(cfg, coach, voice):
    """Feed synthetic laps through the whole pipeline - no iRacing needed."""
    from .demo import make_lap

    voice.start()
    session = CoachSession(cfg, coach, voice)
    print("DEMO MODE - synthetic laps, no iRacing needed.\n")
    laps = [make_lap(lap_num=1, phase=0.0),
            make_lap(lap_num=2, slow_corner=1, brake_shift=0.012, phase=0.4),
            make_lap(lap_num=3, slow_corner=2, phase=0.7)]
    for lap in laps:
        session.on_lap_complete(lap, wait=True)
    voice.wait_idle(20)
    session.finish()
    return 0


def run_live(cfg, coach, voice):
    try:
        from .telemetry import IRacingTelemetry
    except ImportError as e:  # pragma: no cover
        print(e)
        return 1

    session = CoachSession(cfg, coach, voice)
    voice.start()
    print(f"LapVoice v{__version__}")

    if cfg["coach"]["use_llm"]:
        ok, msg = coach.check()
        print(("Ready: " if ok else "WARNING: ") + msg)
        if ok:
            threading.Thread(target=coach.warm_up, daemon=True).start()
        elif cfg["coach"]["fallback_to_template"]:
            print("Continuing with simple built-in tips until the AI model is available.")
    else:
        print("AI model disabled - using simple built-in tips.")

    print("Get on track. Your first clean lap becomes the baseline. Ctrl+C to stop.\n")
    try:
        telemetry = IRacingTelemetry(cfg["telemetry"]["poll_hz"],
                                     on_session_change=session.on_session_change)
    except ImportError as e:  # pragma: no cover
        print(e)
        return 1
    try:
        telemetry.poll_loop(session.on_lap_complete)
    except KeyboardInterrupt:
        print("\nStopping...")
        session.finish()
    finally:
        telemetry.shutdown()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="iracing_coach",
                                description="Fully local, spoken AI lap coaching for iRacing.")
    p.add_argument("--config", help="path to a config file")
    p.add_argument("--model", help="Ollama model to use (overrides config)")
    p.add_argument("--no-voice", action="store_true", help="print tips instead of speaking")
    p.add_argument("--no-llm", action="store_true", help="use simple built-in tips, no AI model")
    p.add_argument("--check", action="store_true", help="test your setup and exit")
    p.add_argument("--demo", action="store_true", help="run on synthetic laps (no iRacing)")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = p.parse_args(argv)

    try:
        cfg, used = load_config(args.config)
    except ConfigError as e:
        print(f"Config problem: {e}")
        return 2
    if args.model:
        cfg["ollama"]["model"] = args.model
    if args.no_voice:
        cfg["voice"]["enabled"] = False
    if args.no_llm:
        cfg["coach"]["use_llm"] = False

    coach, voice = build_parts(cfg)
    if args.check:
        return run_check(cfg, used, coach, voice)
    if args.demo:
        return run_demo(cfg, coach, voice)
    return run_live(cfg, coach, voice)
