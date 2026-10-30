"""发不发的规矩（纯函数，测试直接用）：开关、静默时段、每个会话和全部的限额、去重。"""
import time

DEFAULTS = {"on": False, "remote": "", "port": 22, "client": "", "quiet": "00:30-08:30", "per_session": 3, "per_all": 10,
            "urgent_per_night": 2, "dedupe_min": 30, "text_max": 500, "link": ""}


def clean_settings(cur: dict, new: dict) -> dict:
    out = {**DEFAULTS, **cur}
    for k, v in new.items():
        if k not in DEFAULTS or v is None:
            continue
        if isinstance(DEFAULTS[k], bool):
            out[k] = bool(v)
        elif isinstance(DEFAULTS[k], int):
            out[k] = max(0, min(int(v), 65535 if k == "port" else 1000))
        else:
            out[k] = str(v).strip()[:200]
    parse_quiet(out["quiet"])
    return out


def parse_quiet(text: str) -> tuple[int, int] | None:
    """「00:30-08:30」→ (30, 510) 分钟；空的是不设静默时段。写错了 ValueError。"""
    text = (text or "").strip()
    if not text:
        return None
    try:
        a, b = text.split("-")
        ah, am, bh, bm = *map(int, a.split(":")), *map(int, b.split(":"))
        if not (0 <= ah < 24 and 0 <= am < 60 and 0 <= bh < 24 and 0 <= bm < 60):
            raise ValueError
    except ValueError:
        raise ValueError(f"静默时段要写成 00:30-08:30：{text}") from None
    return ah * 60 + am, bh * 60 + bm


def in_quiet(quiet: str, now: float) -> bool:
    span = parse_quiet(quiet)
    if not span or span[0] == span[1]:
        return False
    t = time.localtime(now)
    m = t.tm_hour * 60 + t.tm_min
    return span[0] <= m < span[1] if span[0] < span[1] else m >= span[0] or m < span[1]


def judge(cfg: dict, log: list[dict], box: str, text: str, urgent: bool, now: float) -> tuple[str, str]:
    """("send", "") 现在发；("hold", 说明) 存着，静默时段过了再发；("block", 说明) 不发。log 是最近发出去的 {at, box, text, urgent}。"""
    if not cfg["on"]:
        return "block", "QQ 提醒没开（设置在标签页右键菜单的「QQ 提醒设置」里）"
    hour = [x for x in log if now - x["at"] < 3600]
    if cfg["dedupe_min"] and any(x["box"] == box and x["text"] == text and now - x["at"] < cfg["dedupe_min"] * 60 for x in log):
        return "block", f"{cfg['dedupe_min']} 分钟内发过一模一样的，没重发"
    mine = [x for x in hour if x["box"] == box]
    if len(mine) >= cfg["per_session"]:
        back = time.strftime("%H:%M", time.localtime(min(x["at"] for x in mine) + 3600))
        return "block", f"这个会话这一小时已经提醒过 {len(mine)} 次，{back} 以后才能再发"
    if len(hour) >= cfg["per_all"]:
        return "block", f"这一小时大家一共提醒了 {len(hour)} 次，到上限了"
    if in_quiet(cfg["quiet"], now):
        night = [x for x in log if x.get("urgent") and in_quiet(cfg["quiet"], x["at"]) and now - x["at"] < 12 * 3600]
        if not urgent or len(night) >= cfg["urgent_per_night"]:
            return "hold", f"现在是静默时段（{cfg['quiet']}），信存着，过了合成一条发"
    return "send", ""
