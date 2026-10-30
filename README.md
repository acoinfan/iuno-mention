# iuno-mention：让会话在 QQ 上提醒你

查看器（[iuno-viewer](https://github.com/acoinfan/iuno-viewer)）里的会话有重要结果时，用信箱发信给 `空白@qq`（`空白` 是你的称呼，写什么都发给你），
消息经你的 gscore 机器人私聊到你的 QQ；你回 `#编号 内容`，回复作为一封信回到发提醒的那个会话，并叫醒它。

这一个仓库有两半：

| 目录 | 是什么 | 装在哪 |
| --- | --- | --- |
| `gscore/IunoMention/` | gscore 插件：私聊你、收你的回复 | 跑 gscore 的服务器 |
| `relay/` | SSH 强制命令跑的中转：只传话 | 同上 |
| `viewer/` | 查看器的第三方扩展：把关、接进信箱 | 跑查看器的机器 |
| `install/install.sh` | 服务器这一半的安装脚本 | 同上 |

## 装

**服务器上**（有 gscore、能 sudo）：

```bash
git clone https://github.com/acoinfan/iuno-mention.git && cd iuno-mention
bash install/install.sh        # 会重启 gscore，机器人断开十几秒。plugins 目录不在默认位置时：PLUGINS=… bash install/install.sh
```

**查看器上**：设置 → 扩展里搜「QQ 提醒」点安装（带后端，要确认一次），或者在终端：

```bash
iunoctl ext install https://github.com/acoinfan/iuno-mention.git --ref v0.1.0
iunoctl viewer restart
```

**连起来**：标签页右键 →「QQ 提醒设置」→「生成钥匙」，把它给的 `--add-client` 那条命令拿到服务器的仓库目录里运行；再填地址（`iuno-mention@你的服务器`）、打开总开关、点「发一条测试」。

## 用

- 会话里：信箱的 `send`，`to` 写 `空白@qq`。可以和别的收件人写在一个数组里群发。信上 `wake=true` 算紧急：静默时段也照发（一晚最多 2 条）。
- 网页上：信箱面板写信，「发给」里挑 `空白@qq`。
- QQ 上：回 `#编号 内容`。另外认三句：`尤诺提醒 状态`、`尤诺提醒 暂停 2h`、`尤诺提醒 恢复`。

## 把关

都在查看器这边，钥匙也在这边，容器里的 agent 碰不到：总开关（默认关）、静默时段（默认 00:30–08:30，存着，过了每个会话合成一条）、
每个会话每小时 3 条、全部每小时 10 条、相同内容 30 分钟不重发、正文 500 字。被拦下的信在信箱的「发出的信」里写着原因。

## 隐私和安全

- 消息经过你的服务器和腾讯。默认只发文字，不发文件和图片。
- 服务器只传话。它被攻破时，最坏是有人冒充你回复：查看器只认自己发出去过、48 小时内、没回过的编号，回复最长 1000 字。
- `iuno-mention` 用户只能跑中转，不能开终端、不能转发端口。吊销：`bash install/install.sh --remove-client 名字`。

## 开发

```bash
uv run --no-project --with pytest --with fastapi --with pydantic python -m pytest tests
```

版本号和 iuno-viewer 的规矩一样（`RELEASING.md`、`docs/扩展开发.md`）：标签 `vX.Y.Z`，要和 `viewer/manifest.json` 的 `version`、`gscore/IunoMention/pyproject.toml` 的 `version` 一致。协议见 [docs/protocol.md](docs/protocol.md)。
