"""Turns lap findings into a short spoken tip using a local Ollama model.

Everything stays on your machine. If the model is unavailable, a simple
built-in (non-AI) tip is used instead, so the tool still helps.
"""

from __future__ import annotations

import re

import requests

SYSTEM_PROMPT = """You are a terse, expert sim racing coach on the radio, talking to a driver mid-session in iRacing.
You get a comparison of the driver's last lap against their best lap. Regions are given as a percentage of the way around the lap.
Reply with ONE short, specific, actionable tip about the biggest time loss. Under 25 words. Plain spoken English only: no markdown, no lists, no greeting, no filler.

Example input:
Lap 7: 1:32.41, 0.31s slower than best lap.
- 34-41% of the lap: lost 0.21s, minimum speed 6 km/h lower, braked earlier than on your best lap
Example reply:
You're braking too early about a third of the way round. Carry more speed to the apex.

Example input:
Lap 3: 58.12, 0.18s slower than best lap.
- 70-76% of the lap: lost 0.14s, less throttle than on your best lap
Example reply:
Get back on the power sooner at about seventy percent. You're late picking up the throttle on exit."""


# ---------------------------------------------------------------- formatting
def fmt_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:05.2f}" if m >= 1 else f"{s:.2f}"


def spoken_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)} minute {s:.2f}" if m >= 1 else f"{s:.2f}"


def _speed(ms: float, units: str):
    return (ms * 2.23694, "mph") if units == "mph" else (ms * 3.6, "km/h")


def describe_finding(f: dict, units: str = "kmh") -> str:
    parts = [f"lost {f['time_lost']:.2f}s"]
    if f.get("min_speed_delta") is not None and f["min_speed_delta"] < -0.3:
        v, label = _speed(abs(f["min_speed_delta"]), units)
        parts.append(f"minimum speed {v:.0f} {label} lower")
    for key in ("brake_note", "throttle_note"):
        if f.get(key):
            parts.append(f[key])
    return f"{f['start_pct']}-{f['end_pct']}% of the lap: " + ", ".join(parts)


def template_tip(findings: list, units: str = "kmh") -> str | None:
    """Simple non-AI tip, used when the LLM is off or unavailable."""
    if not findings:
        return None
    f = findings[0]
    text = f"You lost {f['time_lost']:.2f} seconds between {f['start_pct']} and {f['end_pct']} percent of the lap."
    detail = f.get("brake_note") or f.get("throttle_note")
    if detail:
        text += f" You {detail}."
    return text


def clean_reply(text: str, max_sentences: int = 1, max_words: int = 35) -> str:
    text = re.sub(r"[*_#`>\"\u201c\u201d]+", "", text or "")
    text = re.sub(r"\s+", " ", text).strip().strip("'")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = " ".join(sentences[:max_sentences])
    if len(out.split()) < 5 and len(sentences) > max_sentences:
        out = " ".join(sentences[:max_sentences + 1])
    words = out.split()
    if len(words) > max_words:
        out = " ".join(words[:max_words]).rstrip(",;:") + "."
    return out


def model_installed(name: str, installed: list) -> bool:
    if name in installed:
        return True
    return ":" not in name and f"{name}:latest" in installed


# --------------------------------------------------------------------- coach
class LocalCoach:
    def __init__(self, host, model, temperature=0.4, max_tokens=60, num_ctx=2048,
                 keep_alive="30m", timeout=12, units="kmh",
                 use_llm=True, fallback_to_template=True):
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.num_ctx = num_ctx
        self.keep_alive = keep_alive
        self.timeout = timeout
        self.units = units
        self.use_llm = use_llm
        self.fallback = fallback_to_template
        self.http = requests.Session()
        self._warned: set = set()

    # -- setup checks --
    def check(self):
        """(ok, message) - is Ollama running and is the model installed?"""
        try:
            r = self.http.get(f"{self.host}/api/tags", timeout=3)
            r.raise_for_status()
        except requests.exceptions.RequestException:
            return False, (f"Can't reach Ollama at {self.host}. Install it from "
                           "https://ollama.com/download and make sure it's running.")
        names = [m.get("name", "") for m in r.json().get("models", [])]
        if not model_installed(self.model, names):
            return False, f"Model '{self.model}' isn't installed. Run:  ollama pull {self.model}"
        return True, f"Ollama is running and '{self.model}' is installed"

    def warm_up(self):
        """Load the model into VRAM now so the first real tip isn't slow."""
        self._generate("Reply with the single word: ready", system=None, max_tokens=3,
                       timeout=max(self.timeout, 60))

    # -- generation --
    def _generate(self, prompt, system=SYSTEM_PROMPT, max_tokens=None, timeout=None):
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                "num_predict": max_tokens or self.max_tokens,
                "num_ctx": self.num_ctx,
            },
        }
        if system:
            payload["system"] = system
        try:
            resp = self.http.post(f"{self.host}/api/generate", json=payload,
                                  timeout=timeout or self.timeout)
            if resp.status_code == 404:
                print(f"[coach] Ollama doesn't have model '{self.model}'. "
                      f"Run: ollama pull {self.model}")
                return None
            resp.raise_for_status()
            return (resp.json().get("response") or "").strip() or None
        except requests.exceptions.ConnectionError:
            if "conn" not in self._warned:  # say it once, not every lap
                self._warned.add("conn")
                print(f"[coach] Can't reach Ollama at {self.host} - is it running? "
                      "Using built-in tips until it is.")
            return None
        except requests.exceptions.RequestException as e:
            print(f"[coach] No reply from Ollama ({type(e).__name__}). "
                  "The model may be too slow - see the README for smaller models.")
            return None

    def lap_tip(self, lap_num, lap_time, delta, findings):
        if not findings:
            return None
        if self.use_llm:
            direction = "slower" if delta > 0 else "faster"
            lines = [f"Lap {lap_num}: {fmt_time(lap_time)}, {abs(delta):.2f}s {direction} than best lap."]
            lines += ["- " + describe_finding(f, self.units) for f in findings]
            reply = self._generate("\n".join(lines) + "\nReply:")
            if reply:
                cleaned = clean_reply(reply)
                if cleaned:
                    return cleaned
        return template_tip(findings, self.units) if (self.fallback or not self.use_llm) else None

    def session_summary(self, stats):
        if not stats:
            return None
        fallback = (f"Session done. {stats['laps']} laps, best {fmt_time(stats['best'])}, "
                    f"average {fmt_time(stats['avg'])}.")
        if not self.use_llm:
            return fallback
        prompt = (
            f"Session finished: {stats['laps']} clean laps, best {fmt_time(stats['best'])}, "
            f"average {fmt_time(stats['avg'])}, slowest {fmt_time(stats['worst'])}, "
            f"lap-to-lap spread {stats['spread']:.2f}s.\n"
            "Give a short two-sentence wrap-up: one thing that went well and the single "
            "most useful focus for next session. Plain spoken English, no lists.")
        reply = self._generate(prompt, max_tokens=80, timeout=max(self.timeout, 20))
        return clean_reply(reply, max_sentences=2, max_words=50) if reply else fallback
