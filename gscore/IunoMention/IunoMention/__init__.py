"""把查看器发来的提醒私聊给主人，把主人的回复交回去。只传话；发多少、什么时候发由查看器那边管。
只认 masters 里的 QQ、只认私聊；整句匹配，不吞别的插件的命令。"""
import re
import time

from gsuid_core.aps import scheduler
from gsuid_core.bot import Bot
from gsuid_core.config import core_config
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.sv import SV, Plugins
from gsuid_core.utils.message import send_msg_to_master

from .core import (Book, CMD_RE, REPLY_RE, hand_reply, next_batch, over_limit, parse_pause, paused_until, render, set_paused,
                   SPOOL, status_text, to_ascii_digits)

Plugins(name="IunoMention", allow_empty_prefix=True)

sv = SV("尤诺提醒", pm=0, priority=1, area="DIRECT")
book = Book()


def from_master(ev: Event) -> bool:
    masters = [str(m) for m in core_config.get_config("masters")]
    return ev.user_type == "direct" and str(ev.user_id) in masters


@sv.on_regex(REPLY_RE.pattern, block=True)
async def reply(bot: Bot, ev: Event) -> None:
    if not from_master(ev):
        return
    m = REPLY_RE.match(ev.raw_text or ev.text or "")
    if not m:
        return
    if why := hand_reply(to_ascii_digits(m[1]), m[2], str(ev.user_id), SPOOL, book):
        await bot.send(why)
    else:
        await bot.send(f"收到，#{to_ascii_digits(m[1])} 已转回去")


@sv.on_regex(CMD_RE, block=True)
async def command(bot: Bot, ev: Event) -> None:
    if not from_master(ev):
        return
    text = (ev.raw_text or ev.text or "").strip()
    if text.endswith("状态"):
        await bot.send(status_text())
    elif text.endswith("恢复"):
        set_paused(0)
        await bot.send("恢复了")
    elif (secs := parse_pause(text)) is not None:
        until = time.time() + secs
        set_paused(until)
        await bot.send(f"暂停到 {time.strftime('%m-%d %H:%M', time.localtime(until))}，查看器那边会把信存着")


@scheduler.scheduled_job("interval", seconds=2, id="iuno_mention_pump", max_instances=1, coalesce=True)
async def pump() -> None:
    now = time.time()
    if paused_until() > now:
        return
    for path, msg in next_batch():
        client = str(msg.get("client") or "")
        if not client or over_limit(SPOOL, client, now):
            path.unlink(missing_ok=True)
            logger.warning(f"[尤诺提醒] {client or '不明客户端'} 的一条没发：来源不对，或者一小时内发得太多")
            continue
        try:
            number = book.add(client, msg.get("id"))
            await send_msg_to_master(re.sub(r"\r", "", render(msg, number)))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[尤诺提醒] 私聊主人失败，下一轮再试：{e}")
            return
        path.unlink(missing_ok=True)
