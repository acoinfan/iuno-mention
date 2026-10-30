"""插件的逻辑（不依赖 gscore）和中转（真的跑那个脚本，队列放临时目录）。"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mention_core", ROOT / "gscore/IunoMention/IunoMention/core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


@pytest.fixture
def spool(tmp_path):
    for d in ("to_qq", "from_qq", "alive", "owners"):
        (tmp_path / d).mkdir()
    return tmp_path


def test_numbers_are_unique_across_clients_and_taken_once(spool):
    book = core.Book(spool)
    a, b = book.add("pc", 12), book.add("mac", 12)
    assert a != b
    assert book.take(a) == {"client": "pc", "id": 12, "at": pytest.approx(time.time(), abs=5)}
    assert book.take(a) is None


def test_numbers_expire(spool):
    book = core.Book(spool)
    n = book.add("pc", 1)
    data = json.loads((spool / "numbers.json").read_text())
    data[n]["at"] -= core.EXPIRE + 1
    (spool / "numbers.json").write_text(json.dumps(data))
    assert book.take(n) is None


def test_reply_goes_to_the_right_client_folder(spool):
    book = core.Book(spool)
    n = book.add("pc", 7)
    assert core.hand_reply(n, "好的", "12345", spool, book) == ""
    got = json.loads(next((spool / "from_qq" / "pc").glob("*.json")).read_text())
    assert (got["id"], got["text"]) == (7, "好的") and "12345" not in got["from"]
    assert "没有" in core.hand_reply(n, "再回一次", "12345", spool, book)


def test_reply_regex_is_narrow():
    assert core.REPLY_RE.match("#12 好的").groups() == ("12", "好的")
    assert core.REPLY_RE.match("＃１２ 好的")
    assert not core.REPLY_RE.match("#12")
    assert not core.REPLY_RE.match("今天 #12 好的")
    assert not core.REPLY_RE.match("#abc 好的")


def test_hour_limit_and_pause(spool):
    now = time.time()
    assert not any(core.over_limit(spool, "pc", now) for _ in range(core.HOUR_MAX))
    assert core.over_limit(spool, "pc", now)
    assert not core.over_limit(spool, "mac", now)
    assert core.parse_pause("尤诺提醒 暂停 2h") == 7200
    assert core.parse_pause("尤诺提醒 暂停30分钟") == 1800
    assert core.parse_pause("尤诺提醒 暂停") == 7200
    assert core.parse_pause("暂停") is None


def relay(spool, *lines, client="pc", wait=1.0):
    env = {**os.environ, "IUNO_MENTION_SPOOL": str(spool)}
    p = subprocess.Popen([sys.executable, "-I", str(ROOT / "relay/iuno-mention-relay"), client], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         text=True, env=env)
    for line in lines:
        p.stdin.write(json.dumps(line, ensure_ascii=False) + "\n")
        p.stdin.flush()
    time.sleep(wait)
    return p


def rows(p):
    p.stdin.close()
    out = [json.loads(x) for x in p.stdout.read().splitlines()]
    p.wait()
    return out


def test_relay_handshake_queue_and_reply(spool):
    p = relay(spool, {"op": "hello", "proto": 1}, {"op": "send", "id": 5, "title": "实验", "text": "跑完了"})
    book = core.Book(spool)
    msg = core.next_batch(spool)[0][1]
    assert (msg["client"], msg["id"], msg["text"]) == ("pc", 5, "跑完了")
    n = book.add("pc", 5)
    core.hand_reply(n, "继续", "1", spool, book)
    time.sleep(1)
    got = rows(p)
    assert got[0] == {"op": "hello", "proto": 1, "client": "pc"}
    assert {"op": "ack", "id": 5, "status": "queued"} in got
    assert [x for x in got if x["op"] == "reply"][0]["text"] == "继续"
    assert (spool / "alive" / "pc").exists()


def test_relay_refuses_wrong_protocol_and_bad_client(spool):
    assert rows(relay(spool, {"op": "hello", "proto": 99}))[0]["op"] == "error"
    assert rows(relay(spool, {"op": "hello", "proto": 1}, client="../evil"))[0]["op"] == "error"


def test_relay_says_paused_and_does_not_queue(spool):
    core.set_paused(time.time() + 100, spool)
    got = rows(relay(spool, {"op": "hello", "proto": 1}, {"op": "send", "id": 5, "text": "x"}))
    assert {"op": "ack", "id": 5, "status": "paused"} in got
    assert not core.next_batch(spool)
