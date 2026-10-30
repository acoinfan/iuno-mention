"""插件的逻辑，不依赖 gscore（测试直接用）：队列目录的读写、QQ 上的编号、发出去的样子。

编号由这里给：几台机器（客户端）各自的编号会撞，QQ 上要唯一。编号记着是哪个客户端的哪一封，48 小时后作废。"""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

SPOOL = Path(os.environ.get("IUNO_MENTION_SPOOL", "/var/lib/iuno-mention"))
EXPIRE = 48 * 3600
HOUR_MAX = 30          # 每个客户端一小时最多发这么多（查看器那边另有限额，这里兜底）
TEXT_MAX = 1500
REPLY_MAX = 1000
REPLY_RE = re.compile(r"^\s*[#＃]\s*([0-9０-９]{1,4})\s+(.+?)\s*$", re.S)
PAUSE_RE = re.compile(r"^\s*尤诺提醒\s*暂停(?:\s*([0-9]{1,3})\s*([hHmM小时分钟]*))?\s*$")
CMD_RE = r"^\s*尤诺提醒\s*(状态|恢复|暂停(\s*[0-9]{1,3}\s*[hHmM小时分钟]*)?)\s*$"
CLIENT_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data):
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class Book:
    """编号簿（只有插件读写）：number → {client, id, at}。"""

    def __init__(self, spool: Path = SPOOL):
        self.spool, self.path = spool, spool / "numbers.json"

    def load(self) -> dict:
        now = time.time()
        return {k: v for k, v in _read(self.path, {}).items() if now - v.get("at", 0) < EXPIRE}

    def add(self, client: str, mid) -> str:
        book = self.load()
        for _ in range(9999):
            last = _read(self.spool / "last-number.json", 0)
            n = str(last % 9999 + 1)
            _write(self.spool / "last-number.json", int(n))
            if n not in book:
                break
        else:
            raise RuntimeError("编号用完了")
        book[n] = {"client": client, "id": mid, "at": time.time()}
        _write(self.path, book)
        return n

    def take(self, number: str):
        """回复只收一次：拿到就从簿子里划掉。"""
        book = self.load()
        got = book.pop(number, None)
        if got is not None:
            _write(self.path, book)
        return got


def to_ascii_digits(text: str) -> str:
    return text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def render(msg: dict, number: str) -> str:
    title = str(msg.get("title") or "")[:40]
    link = f" · {msg['link']}" if msg.get("link") else ""
    return (f"【尤诺{'·' + title if title else ''}】#{number}\n{str(msg.get('text') or '')[:TEXT_MAX]}\n"
            f"—— 回「#{number} 内容」答复这条{link} · {msg.get('client') or ''}")


def over_limit(spool: Path, client: str, now: float) -> bool:
    """一小时内这个客户端发够了没有；没发够就记一笔。"""
    path = spool / "sent-times.json"
    data = _read(path, {})
    recent = [t for t in data.get(client, []) if now - t < 3600]
    if len(recent) >= HOUR_MAX:
        return True
    data[client] = recent + [now]
    _write(path, data)
    return False


def next_batch(spool: Path = SPOOL) -> list[tuple[Path, dict]]:
    out = []
    for path in sorted((spool / "to_qq").glob("*.json")):
        msg = _read(path, None)
        out.append((path, msg if isinstance(msg, dict) else {}))
    return out


def paused_until(spool: Path = SPOOL) -> float:
    return float(_read(spool / "paused.json", 0) or 0)


def set_paused(until: float, spool: Path = SPOOL):
    _write(spool / "paused.json", until)


def parse_pause(text: str) -> int | None:
    """「尤诺提醒 暂停 2h」→ 秒数；没写多久是 2 小时；不是这句话是 None。"""
    m = PAUSE_RE.match(text)
    if not m:
        return None
    n, unit = int(m[1] or 2), (m[2] or "h").lower()
    return n * 60 if unit[0] in ("m", "分") else n * 3600


def status_text(spool: Path = SPOOL, now: float | None = None) -> str:
    now = now or time.time()
    lines = []
    for path in sorted((spool / "alive").glob("*")):
        try:
            gap = now - float(path.read_text())
        except (OSError, ValueError):
            continue
        lines.append(f"{path.name}：{'在线' if gap < 20 else f'{int(gap // 60)} 分钟前断开'}")
    until = paused_until(spool)
    if until > now:
        lines.append(f"暂停到 {time.strftime('%m-%d %H:%M', time.localtime(until))}")
    return "尤诺提醒\n" + ("\n".join(lines) or "还没有客户端连上来")


def hand_reply(number: str, text: str, user: str, spool: Path = SPOOL, book: Book | None = None) -> str:
    """主人回了一条：交回发这条的客户端。返回给主人看的话（出错时）；空的是交好了。"""
    got = (book or Book(spool)).take(number)
    if got is None:
        return f"没有 #{number} 这一条（48 小时前的、回过的都作废了）"
    client = got["client"]
    if not CLIENT_RE.fullmatch(client):
        return "这条的来源不对，没转"
    folder = spool / "from_qq" / client
    folder.mkdir(mode=0o2770, exist_ok=True)
    _write(folder / f"{time.time_ns()}-{uuid.uuid4().hex[:8]}.json",
           {"id": got["id"], "text": text[:REPLY_MAX], "at": time.time(), "from": hashlib.sha256(user.encode()).hexdigest()[:16]})
    return ""
