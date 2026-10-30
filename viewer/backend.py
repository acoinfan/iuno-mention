"""QQ 提醒（iuno-mention 的查看器这一半，丙类扩展）：接成信箱的远端通道 qq，收件人写「空白@qq」就发到主人的 QQ。

- 把关全在这里（gates.py）：总开关、静默时段、限额、去重。钥匙在查看器的数据目录，容器里的 agent 读不到，绕不过去。
- 查看器进程常驻一条 ssh 连接到 remote 的中转（docs/protocol.md），断了隔 RETRY 秒重连；没连上时信排着，连上再发。
- 主人在 QQ 上回「#编号 内容」：回到发这条提醒的会话（信箱按 to 分发），盖「本人在 QQ 上回复」的章（trusted_stamp）。
  只认自己发出去过、48 小时内的编号，每个编号只收一次，remote 被攻破也塞不进别的话。
- 数据都在 ctx.data：settings.json、state.json（排着的信、发送记录、编号）、id_ed25519、known_hosts。"""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

from fastapi import HTTPException
from pydantic import BaseModel

from .gates import DEFAULTS, clean_settings, judge

CHANNEL = "qq"
PROTO = 1
RETRY = 30
ALIVE_SECS = 15
REPLY_TTL = 48 * 3600
LOG_MAX = 200


class State:
    """排着的信、发送记录、编号：存在一个 JSON 里，所有读写过这把锁。"""

    def __init__(self, path: Path):
        self.path, self.lock = path, threading.RLock()
        try:
            self.d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.d = {}
        for k, v in (("next", 1), ("outbox", []), ("held", []), ("log", []), ("sent", {}), ("replies", [])):
            self.d.setdefault(k, v)

    def save(self):
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)


class Transport:
    """信箱的远端通道。信箱在线程里调这些方法。"""
    targeted, trusted_stamp = True, True      # 没人开「收远端的信」也拉；它盖的章信箱认

    def __init__(self, ctx, st: State, settings):
        self.ctx, self.st, self.settings = ctx, st, settings
        self.alive_at, self.paused, self.error, self.proc = 0.0, False, "", None
        self.write_lock = threading.Lock()

    # ------------------------------------------------------------ 信箱要的四个方法，外加 send_from
    def peers(self):
        return [{"name": self.ctx.call_of(""), "online": self.online(), "last_prompt": None}]

    def send(self, to, type, body, refs, needs_reply, reply_to):     # 没有 send_from 的老信箱才走这里
        return self.send_from(box="查看器", sid="", title="", to=to, type=type, body=body, refs=refs,
                              needs_reply=needs_reply, reply_to=reply_to, wake=None)

    def send_from(self, box, sid, title, to, type, body, refs, needs_reply, reply_to, wake):
        cfg, now = self.settings(), time.time()
        text = body if len(body) <= cfg["text_max"] else body[:cfg["text_max"]] + "…（全文在查看器里看）"
        urgent = bool(wake)
        with self.st.lock:
            action, why = judge(cfg, [x for x in self.st.d["log"] if x.get("counted")], box, text, urgent, now)
            if action == "block":
                self.note(box, text, "没发：" + why)
                return {"sent": False, "note": why}
            if action == "hold":
                self.st.d["held"].append({"box": box, "title": title, "text": text, "at": now})
                self.note(box, text, "存着：" + why)
                self.st.save()
                return {"sent": True, "note": why}
            self.enqueue(box, title, text, urgent, now)
        self.flush()
        return {"sent": True, "note": "发到 QQ 了" if self.online() else "中转现在没连上，连上就发"}

    def inbox(self, include_acked, limit):
        with self.st.lock:
            return [dict(m) for m in self.st.d["replies"]][:limit]

    def ack(self, ids):
        with self.st.lock:
            self.st.d["replies"] = [m for m in self.st.d["replies"] if m["id"] not in ids and str(m["id"]) not in map(str, ids)]
            self.st.save()

    # ------------------------------------------------------------ 发
    def online(self) -> bool:
        return time.time() - self.alive_at < ALIVE_SECS

    def note(self, box, text, what):
        self.st.d["log"] = (self.st.d["log"] + [{"at": time.time(), "box": box, "text": text, "what": what, "counted": False}])[-LOG_MAX:]
        self.st.save()

    def enqueue(self, box, title, text, urgent, now):
        with self.st.lock:
            mid = self.st.d["next"]
            self.st.d["next"] = mid + 1
            self.st.d["outbox"].append({"id": mid, "box": box, "title": title, "text": text, "urgent": urgent})
            self.st.d["sent"][str(mid)] = {"box": box, "at": now}
            self.st.d["log"] = (self.st.d["log"] + [{"at": now, "box": box, "text": text, "urgent": urgent, "what": "发了", "counted": True}])[-LOG_MAX:]
            self.st.save()

    def flush(self):
        """把排着的信写给中转；没连上就留着。ack 回来才从 outbox 拿掉，重连时重发。"""
        if not self.online() or self.paused:
            return
        with self.st.lock:
            rows = [m for m in self.st.d["outbox"] if not m.get("wrote") or time.time() - m["wrote"] > 60]
            link = self.settings()["link"]
            for m in rows:
                if self.write({"op": "send", "id": m["id"], "title": m["title"], "text": m["text"], "urgent": m["urgent"], **({"link": link} if link else {})}):
                    m["wrote"] = time.time()
            self.st.save()

    def write(self, obj) -> bool:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            with self.write_lock:
                proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    # ------------------------------------------------------------ 收
    def handle(self, msg: dict):
        op = msg.get("op")
        if op == "hello":
            self.alive_at, self.error = time.time(), ""
            self.flush()
        elif op == "alive":
            was = self.paused
            self.alive_at, self.paused = time.time(), bool(msg.get("paused"))
            if was and not self.paused:
                self.flush()
        elif op == "ack":
            if msg.get("status") == "queued":
                with self.st.lock:
                    self.st.d["outbox"] = [m for m in self.st.d["outbox"] if m["id"] != msg.get("id")]
                    self.st.save()
        elif op == "reply":
            self.take_reply(msg)
        elif op == "error":
            self.error = str(msg.get("why") or "中转报错")

    def take_reply(self, msg: dict):
        """只认自己发出去过、没过期、没回过的编号；交给信箱（发回那个会话）。"""
        key, now = str(msg.get("id")), time.time()
        with self.st.lock:
            sent = self.st.d["sent"].pop(key, None)
            self.st.d["sent"] = {k: v for k, v in self.st.d["sent"].items() if now - v["at"] < REPLY_TTL}
            if sent is None or now - sent["at"] >= REPLY_TTL:
                self.st.save()
                self.ctx.log(f"收到编号 {key} 的回复，但这条没发过、回过了或者过期了，丢掉")
                return
            call = self.ctx.call_of("")
            self.st.d["replies"].append({"id": f"r{key}", "from": call, "to": sent["box"], "type": "info",
                                         "body": str(msg.get("text") or "")[:1000], "refs": [], "needs_reply": False,
                                         "stamp": f"查看器核实：这封是{call}本人在 QQ 上回复你的提醒"})
            self.st.save()

    # ------------------------------------------------------------ 连接
    def connect_loop(self, key: Path, known: Path):
        while True:
            cfg = self.settings()
            if cfg["on"] and cfg["remote"] and key.exists():
                self.run_once(cfg, key, known)
                self.alive_at = 0.0
            time.sleep(RETRY)

    def run_once(self, cfg, key: Path, known: Path):
        argv = ["ssh", "-F", "/dev/null", "-i", str(key), "-p", str(cfg["port"] or 22), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
                "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-o", "StrictHostKeyChecking=accept-new",
                "-o", f"UserKnownHostsFile={known}", "-T", cfg["remote"]]
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as e:
            self.error = f"起不了 ssh：{e}"
            return
        self.write({"op": "hello", "proto": PROTO})
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.handle(msg)
        err = (self.proc.stderr.read() or "").strip()
        code = self.proc.wait()
        if not self.error or err:
            self.error = f"连接断了（{code}）：{err[-300:]}" if err or code else ""

    # ------------------------------------------------------------ 静默时段过了：每个会话合成一条发
    def release_held(self, now: float):
        cfg = self.settings()
        with self.st.lock:
            if not self.st.d["held"] or judge(cfg, [], "", "", False, now)[0] == "hold":
                return
            by_box: dict[str, list] = {}
            for m in self.st.d["held"]:
                by_box.setdefault(m["box"], []).append(m)
            self.st.d["held"] = []
            for box, rows in by_box.items():
                self.enqueue(box, rows[0]["title"], "\n---\n".join(r["text"] for r in rows)[:cfg["text_max"] * 3], False, now)
        self.flush()


class SettingsBody(BaseModel):
    on: bool | None = None
    remote: str | None = None
    port: int | None = None
    quiet: str | None = None
    per_session: int | None = None
    per_all: int | None = None
    urgent_per_night: int | None = None
    dedupe_min: int | None = None
    text_max: int | None = None
    link: str | None = None


def setup(ctx):
    data = Path(ctx.data)
    data.mkdir(parents=True, exist_ok=True)
    conf_file, key, known = data / "settings.json", data / "id_ed25519", data / "known_hosts"
    st = State(data / "state.json")

    def settings() -> dict:
        try:
            return {**DEFAULTS, **json.loads(conf_file.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return dict(DEFAULTS)

    t = Transport(ctx, st, settings)
    mailbox = ctx.use("mailbox")
    if mailbox is None:
        ctx.log("信箱扩展没启用：QQ 提醒没地方接")
    else:
        mailbox.register_transport(CHANNEL, t)
    threading.Thread(target=t.connect_loop, args=(key, known), daemon=True, name="iuno-mention").start()

    async def tick():
        t.release_held(time.time())
        t.flush()

    ctx.every(30, tick)

    def client_name() -> str:
        return settings()["client"] or (socket.gethostname().lower().replace("_", "-")[:30] + "-viewer").strip("-")

    def public_key() -> str:
        try:
            return (data / "id_ed25519.pub").read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def status_view() -> dict:
        cfg, pub = settings(), public_key()
        with st.lock:
            log = list(st.d["log"])[-20:]
            counts = {"outbox": len(st.d["outbox"]), "held": len(st.d["held"])}
        return {"settings": cfg, "online": t.online(), "paused": t.paused, "error": t.error, "public_key": pub, "client": client_name(),
                "add_client": f"bash install/install.sh --add-client {client_name()} '{pub}'" if pub else "", "log": log[::-1], **counts}

    @ctx.router.get("/status")
    def status():
        return status_view()

    @ctx.router.post("/settings")
    def save_settings(body: SettingsBody):
        try:
            new = clean_settings(settings(), {k: v for k, v in body.model_dump().items() if v is not None})
        except ValueError as e:
            raise HTTPException(400, str(e)) from None
        tmp = conf_file.with_name(".settings.tmp")
        tmp.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        tmp.replace(conf_file)
        proc = t.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()      # 地址、开关可能变了：断开，重连时按新的
        return status_view()

    @ctx.router.post("/keygen")
    def keygen():
        if key.exists():
            return status_view()
        got = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"iuno-mention-{client_name()}", "-f", str(key)],
                             capture_output=True, text=True, timeout=30)
        if got.returncode:
            raise HTTPException(500, f"钥匙没生成：{got.stderr.strip()[-200:]}")
        return status_view()

    @ctx.router.post("/test")
    def test():
        cfg = settings()
        if not cfg["on"]:
            raise HTTPException(400, "先打开总开关")
        t.enqueue("查看器", "测试", "这是一条测试提醒。回「#编号 内容」试试，回复会丢掉（没有对应的会话）", False, time.time())
        t.flush()
        return status_view()
