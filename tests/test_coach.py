from iracing_coach.coach import (LocalCoach, clean_reply, describe_finding, fmt_time,
                                 model_installed, spoken_time, template_tip)

FINDING = {"start_pct": 34, "end_pct": 41, "time_lost": 0.21, "min_speed_delta": -1.7,
           "brake_note": "braked earlier than on your best lap", "throttle_note": None}


def make_coach(**kw):
    return LocalCoach("http://localhost:11434", "llama3.2:3b", **kw)


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code, self._p = status, payload or {}

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


def test_time_formatting():
    assert fmt_time(23.714) == "23.71"
    assert fmt_time(92.41) == "1:32.41"
    assert spoken_time(92.41) == "1 minute 32.41"


def test_clean_reply_strips_markdown_and_limits():
    out = clean_reply('**"Brake later into turn one." Then more words.**')
    assert out == "Brake later into turn one."
    assert len(clean_reply("word " * 100).split()) <= 36


def test_describe_finding_units():
    assert "6 km/h lower" in describe_finding(FINDING | {"min_speed_delta": -1.7})
    assert "mph lower" in describe_finding(FINDING, "mph")


def test_template_tip():
    assert "0.21 seconds" in template_tip([FINDING])
    assert template_tip([]) is None


def test_model_installed_handles_latest_tag():
    assert model_installed("llama3.2:3b", ["llama3.2:3b"])
    assert model_installed("llama3.2", ["llama3.2:latest"])
    assert not model_installed("llama3.2:3b", ["llama3.1:8b"])


def test_lap_tip_uses_llm_reply(monkeypatch):
    c = make_coach()
    monkeypatch.setattr(c.http, "post", lambda *a, **k: FakeResp(payload={"response": "Brake later at turn one."}))
    assert c.lap_tip(3, 60.0, 0.2, [FINDING]) == "Brake later at turn one."


def test_lap_tip_falls_back_to_template_when_model_missing(monkeypatch):
    c = make_coach()
    monkeypatch.setattr(c.http, "post", lambda *a, **k: FakeResp(status=404))
    assert "0.21 seconds" in c.lap_tip(3, 60.0, 0.2, [FINDING])


def test_no_fallback_means_no_tip(monkeypatch):
    c = make_coach(fallback_to_template=False)
    monkeypatch.setattr(c.http, "post", lambda *a, **k: FakeResp(status=404))
    assert c.lap_tip(3, 60.0, 0.2, [FINDING]) is None


def test_request_caps_context_window(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen.update(json)
        return FakeResp(payload={"response": "ok then go faster now"})

    c = make_coach(num_ctx=2048)
    monkeypatch.setattr(c.http, "post", fake_post)
    c.lap_tip(1, 60.0, 0.1, [FINDING])
    assert seen["options"]["num_ctx"] == 2048 and seen["keep_alive"] == "30m"
