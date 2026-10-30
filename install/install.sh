#!/bin/bash
# iuno-mention 装到 remote 上：在仓库根目录、以能 sudo 的账户运行。
#   bash install/install.sh                          装中转和插件（会重启 gscore，机器人断开十几秒）
#   bash install/install.sh --add-client 名字 '公钥'  登记一台机器（查看器设置里给的那一行）
#   bash install/install.sh --remove-client 名字      吊销
#   bash install/install.sh --uninstall               拆掉（队列目录一起删，钥匙也没了）
# 中转只传话；iuno-mention 用户只能跑中转，不能开终端、不能转发端口。
# 环境变量：PLUGINS（gscore 的 plugins 目录）、GSCORE_UNIT（默认 gscore）、GSCORE_USER（默认 plugins 目录的属主）。
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
RU=iuno-mention
RHOME=/home/$RU
SPOOL=/var/lib/iuno-mention
BIN=/usr/local/bin/iuno-mention-relay
PLUGINS=${PLUGINS:-/opt/bot/gsuid_core/gsuid_core/plugins}
UNIT=${GSCORE_UNIT:-gscore}
KEYS=$RHOME/.ssh/authorized_keys

restart_gscore() {
    start=$(date '+%F %T')
    sudo systemctl restart "$UNIT"
    sleep 12
    systemctl is-active "$UNIT"
    sudo journalctl -u "$UNIT" --since "$start" --no-pager -o cat | grep -i -E "IunoMention|尤诺提醒" | tail -5 || true
}

case "${1:-}" in
--add-client)
    name="${2:-}"; key="${3:-}"
    [[ "$name" =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]] || { echo "名字只能用小写字母、数字和 -" >&2; exit 1; }
    [[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ [^\ ]+)?$ ]] || { echo "公钥格式不对，应该是一行 ssh-ed25519 ..." >&2; exit 1; }
    id "$RU" >/dev/null 2>&1 || { echo "先不带参数运行一次" >&2; exit 1; }
    tmp=$(mktemp)
    sudo grep -v " iuno-mention-client=$name\$" "$KEYS" >"$tmp" || true
    keyonly=$(printf '%s' "$key" | cut -d' ' -f1-2)
    printf 'restrict,command="%s %s" %s iuno-mention-client=%s\n' "$BIN" "$name" "$keyonly" "$name" >>"$tmp"
    sudo install -o root -g root -m 644 "$tmp" "$KEYS"
    rm -f "$tmp"
    echo "登记好了 $name。查看器那边的地址写 $RU@$(hostname -I | cut -d' ' -f1)（有公网地址的话写公网的）"
    exit 0 ;;
--remove-client)
    name="${2:-}"
    tmp=$(mktemp)
    sudo grep -v " iuno-mention-client=$name\$" "$KEYS" >"$tmp" || true
    sudo install -o root -g root -m 644 "$tmp" "$KEYS"
    rm -f "$tmp"
    echo "吊销了 $name"
    exit 0 ;;
--uninstall)
    sudo rm -rf "$PLUGINS/IunoMention"
    restart_gscore
    sudo rm -rf "$SPOOL" "$BIN"
    id "$RU" >/dev/null 2>&1 && sudo userdel "$RU"
    sudo rm -rf "$RHOME"
    echo "拆掉了"
    exit 0 ;;
"") ;;
*) echo "不认识的参数：$1" >&2; exit 1 ;;
esac

[ -d "$PLUGINS" ] || { echo "没有 $PLUGINS：用 PLUGINS=… 指定 gscore 的 plugins 目录" >&2; exit 1; }
OWNER=${GSCORE_USER:-$(stat -c %U "$PLUGINS")}

echo "[1/4] 用户 $RU（家目录和钥匙文件都归 root，它自己改不了）"
if ! id "$RU" >/dev/null 2>&1; then
    sudo useradd --system --home-dir "$RHOME" --no-create-home --shell /bin/sh "$RU"
fi
sudo usermod -p '*' "$RU"   # 没有密码，但不算「锁定」，钥匙登录可用
sudo install -d -o root -g root -m 755 "$RHOME" "$RHOME/.ssh"
[ -f "$KEYS" ] || sudo install -o root -g root -m 644 /dev/null "$KEYS"

echo "[2/4] 中转程序和队列目录"
sudo install -o root -g root -m 755 "$HERE/relay/iuno-mention-relay" "$BIN"
for d in "$SPOOL" "$SPOOL/to_qq" "$SPOOL/from_qq" "$SPOOL/alive" "$SPOOL/owners"; do
    sudo install -d -o "$RU" -g "$OWNER" -m 2770 "$d"
done

echo "[3/4] gscore 插件 IunoMention"
sudo rm -rf "$PLUGINS/IunoMention"
sudo cp -r "$HERE/gscore/IunoMention" "$PLUGINS/IunoMention"
sudo chown -R "$OWNER:$OWNER" "$PLUGINS/IunoMention"

echo "[4/4] 重启 $UNIT"
restart_gscore
echo "完成。下一步：在查看器设置里点「生成钥匙」，把它给的那条 --add-client 命令拿到这里运行。"
