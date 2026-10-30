"""查看器这一半：把关（gates.py）和通道（backend.py 的 Transport），不连 ssh。"""
import importlib
import json
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
gates = importlib.import_module("viewer.gates")
backend = importlib.import_module("viewer.backend")

NIGHT = time.mktime((2026, 10, 30, 2, 0, 0, 0, 0, -1))
DAY = time.mktime((2026, 10, 30, 14, 0, 0, 0, 0, -1))


def cfg(**kw):
    return {**gates.DEFAULTS, "on": True, **kw}


def test_off_and_limits():
    assert gates.judge({**cfg(), "on": False}, [], "甲", "x", False, DAY)[0] == "block"
    log = [{"at": DAY - 10, "box": "甲", "text": str(i)} for i in range(3)]
    action, why = gates.judge(cfg(), log, "甲", "新的", False, DAY)
    assert action == "block" and "这一小时" in why
    assert gates.judge(cfg(), log, "乙", "新的", False, DAY)[0] == "send"
    many = [{"at": DAY - 10, "box": f"b{i}", "text": str(i)} for i in range(10)]
    assert gates.judge(cfg(), many, "乙", "新的", False, DAY)[0] == "block"


def test_dedupe():
    log = [{"at": DAY - 60, "box": "甲", "text": "一样"}]
    assert gates.judge(cfg(), log, "甲", "一样", False, DAY)[0] == "block"
    assert gates.judge(cfg(), [{"at": DAY - 3600, "box": "甲", "text": "一样"}], "甲", "一样", False, DAY)[0] == "send"


def test_quiet_holds_unless_urgent_and_limits_urgent():
    assert gates.judge(cfg(), [], "甲", "x", False, NIGHT)[0] == "hold"
    assert gates.judge(cfg(), [], "甲", "x", True, NIGHT)[0] == "send"
    log = [{"at": NIGHT - i - 1, "box": f"b{i}", "text": str(i), "urgent": True} for i in range(2)]
    assert gates.judge(cfg(), log, "甲", "x", True, NIGHT)[0] == "hold"
    assert gates.judge(cfg(quiet=""), [], "甲", "x", False, NIGHT)[0] == "send"


def test_quiet_crossing_midnight_and_bad_text():
    across = time.mktime((2026, 10, 30, 23, 30, 0, 0, 0, -1))
    assert gates.in_quiet("23:00-07:00", across)
    assert gates.in_quiet("23:00-07:00", NIGHT)
    assert not gates.in_quiet("23:00-07:00", DAY)
    with pytest.raises(ValueError):
        gates.parse_quiet("半夜")
    with pytest.raises(ValueError):
        gates.clean_settings({}, {"quiet": "25:00-26:00"})


class Ctx:
    def call_of(self, sid):
        return "空白"

    def log(self, text):
        self.logged = text


@pytest.fixture
def t(tmp_path):
    written = []
    tr = backend.Transport(Ctx(), backend.State(tmp_path / "state.json"), lambda: cfg(quiet=""))
    tr.alive_at = time.time()
    tr.write = lambda obj: written.append(obj) or True
    tr.written = written
    return tr


def send(tr, box="甲", body="跑完了", wake=None):
    return tr.send_from(box=box, sid="s", title="实验", to="空白", type="info", body=body, refs=[], needs_reply=False, reply_to=None, wake=wake)


def test_send_ack_reply_round_trip(t):
    assert send(t)["sent"]
    mid = t.written[-1]["id"]
    assert t.written[-1]["text"] == "跑完了"
    t.handle({"op": "ack", "id": mid, "status": "queued"})
    assert t.st.d["outbox"] == []
    t.handle({"op": "reply", "id": mid, "text": "继续"})
    got = t.inbox(False, 50)
    assert got[0]["to"] == "甲" and got[0]["body"] == "继续" and "本人在 QQ 上" in got[0]["stamp"]
    t.ack([got[0]["id"]])
    assert t.inbox(False, 50) == []


def test_reply_only_for_numbers_we_sent_and_only_once(t):
    t.handle({"op": "reply", "id": 999, "text": "伪造的"})
    assert t.inbox(False, 50) == []
    send(t)
    mid = t.written[-1]["id"]
    t.handle({"op": "reply", "id": mid, "text": "第一次"})
    t.handle({"op": "reply", "id": mid, "text": "第二次"})
    assert [m["body"] for m in t.inbox(False, 50)] == ["第一次"]


def test_expired_reply_is_dropped(t):
    send(t)
    mid = t.written[-1]["id"]
    t.st.d["sent"][str(mid)]["at"] -= backend.REPLY_TTL + 1
    t.handle({"op": "reply", "id": mid, "text": "太晚了"})
    assert t.inbox(False, 50) == []


def test_offline_keeps_outbox_and_sends_on_hello(t):
    t.alive_at = 0
    assert "没连上" in send(t)["note"]
    assert t.written == [] and len(t.st.d["outbox"]) == 1
    t.handle({"op": "hello", "proto": 1})
    assert len(t.written) == 1


def test_paused_ack_stays_in_outbox(t):
    send(t)
    t.handle({"op": "ack", "id": t.written[-1]["id"], "status": "paused"})
    assert len(t.st.d["outbox"]) == 1


def test_block_and_long_text(t):
    t.settings = lambda: {**cfg(quiet=""), "on": False}
    r = send(t)
    assert not r["sent"] and "没开" in r["note"]
    t.settings = lambda: cfg(quiet="", text_max=5)
    send(t, body="一二三四五六七八")
    assert t.written[-1]["text"].startswith("一二三四五…")


def test_held_merged_per_session_after_quiet(t, monkeypatch):
    t.settings = lambda: cfg()
    monkeypatch.setattr(backend.time, "time", lambda: NIGHT)
    assert "静默" in send(t, "甲", "第一条")["note"]
    send(t, "甲", "第二条")
    send(t, "乙", "第三条")
    assert t.written == []
    t.alive_at = NIGHT
    t.release_held(NIGHT)
    assert t.st.d["held"] != []       # 还在静默时段
    monkeypatch.setattr(backend.time, "time", lambda: DAY)
    t.alive_at = DAY
    t.release_held(DAY)
    assert t.st.d["held"] == []
    assert sorted(x["text"] for x in t.written) == ["第一条\n---\n第二条", "第三条"]


def test_blocked_and_held_do_not_count_against_limits(t):
    t.settings = lambda: cfg(quiet="", per_session=1)
    send(t, body="第一条")
    assert not send(t, body="第二条")["sent"]        # 到上限了
    t.settings = lambda: cfg(quiet="", per_session=2)
    assert send(t, body="第三条")["sent"]            # 刚才被拦的那条不算一条
