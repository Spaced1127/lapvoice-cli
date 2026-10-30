"""Offline text-to-speech (Windows built-in voices via pyttsx3).

Runs on a background thread with a tiny queue. Stale tips are dropped so you
never hear feedback about a lap you finished minutes ago.
"""

from __future__ import annotations

import queue
import threading
import time


class Voice:
    def __init__(self, enabled=True, rate=185, volume=1.0, voice_name_contains=""):
        self.enabled = enabled
        self.rate = rate
        self.volume = volume
        self.voice_name_contains = voice_name_contains
        self._queue: queue.Queue = queue.Queue(maxsize=3)

    def start(self):
        if self.enabled:
            threading.Thread(target=self._worker, daemon=True).start()

    def say(self, text, max_age_sec=15):
        if not self.enabled or not text:
            return
        if self._queue.full():  # freshness beats completeness
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                pass
        self._queue.put((text, time.time() + max_age_sec))

    def wait_idle(self, timeout=20):
        """Block until queued speech has finished (or timeout)."""
        end = time.time() + timeout
        while self.enabled and self._queue.unfinished_tasks and time.time() < end:
            time.sleep(0.1)

    def _worker(self):
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", self.rate)
            engine.setProperty("volume", self.volume)
            if self.voice_name_contains:
                for v in engine.getProperty("voices"):
                    if self.voice_name_contains.lower() in v.name.lower():
                        engine.setProperty("voice", v.id)
                        break
        except Exception as e:
            print(f"[voice] Text-to-speech unavailable ({e}). Tips will be printed only.")
            self.enabled = False
            return

        while True:
            text, expires_at = self._queue.get()
            try:
                if time.time() <= expires_at:
                    engine.say(text)
                    engine.runAndWait()
            except Exception as e:
                print(f"[voice] Speech error: {e}")
            finally:
                self._queue.task_done()
