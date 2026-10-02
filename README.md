# astrbot_plugin_funnypic · 吊图放送

> 从本地图库随机抽图，十连抽合并转发不刷屏，群友还能回复投稿把图库养肥。

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.9.2-5B6EF5.svg)](https://github.com/AstrBotDevs/AstrBot)

一个给 AstrBot 用的轻量吊图插件：图库就是本地一个文件夹，不依赖任何第三方图源、不调用大模型，响应即时。

## 功能

- **随机单抽**：`/吊图` 随机发一张图库里的图，默认 10 秒冷却。
- **随机十连**：`/吊图十连` 一次抽最多 10 张、同一批互不重复，用合并转发消息发出，不会刷屏，默认 180 秒冷却。
- **用户投稿**：回复一张图片再发 `/添加吊图`，图片就进图库了。
- **自动去重**：投稿图片按内容哈希落盘，同一张图换个文件名再投也不会重复入库。
- **图库自建**：图库目录不存在时自动创建，也可以直接把图片拷进去。
- **冷却可调**：两个冷却时间都能在 WebUI 的插件配置里改，填 `0` 就是关闭冷却。

三个指令都不需要加 `/` 前缀，直接发送命令名也可以。

## 效果预览

```
用户> /吊图
机器人> [图片]

用户> /吊图十连
机器人> [合并转发消息 · 吊图放送]
         ├─ [图片]
         ├─ [图片]
         └─ ... 共 10 张
机器人> （图库里只有 6 张图，就先抽这么多了~）

用户> /吊图
机器人> 别急，冷却时间还有 7 秒哦！

用户> /吊图
机器人> 图库是空的哦，快用 '添加吊图' 功能来丰富它吧！

用户> （回复一张图片）/添加吊图
机器人> 好耶！新的吊图已添加！

用户> （再回复同一张图）/添加吊图
机器人> 这张图我好像已经有了哦。
```

## 安装

### 前置条件

- AstrBot `>= 4.9.2`
- 走 OneBot v11 协议的适配器（例如 [NapCat](https://github.com/NapNeko/NapCatQQ) + `aiocqhttp`），十连抽的合并转发依赖它

### 方式一：插件市场

在 WebUI 的「插件市场 → 安装插件」里填入本仓库地址：

```
https://github.com/10XuQ/astrbot_plugin_funnypic
```

### 方式二：手动安装

把 `astrbot_plugin_funnypic` 整个目录放进 AstrBot 的 `data/plugins/` 下：

```
AstrBot/
└── data/
    ├── plugins/
    │   └── astrbot_plugin_funnypic/
    │       ├── main.py
    │       ├── metadata.yaml
    │       ├── _conf_schema.json
    │       ├── logo.png
    │       └── README.md
    └── plugin_data/
        └── astrbot_plugin_funnypic/   ← 图库（首次运行时自动创建）
```

放好之后重启 AstrBot，或者到 WebUI 的「插件管理」里点一下刷新按钮。

## 使用

| 指令 | 说明 | 默认冷却 |
| --- | --- | --- |
| `/吊图` | 随机抽一张吊图 | 10 秒 |
| `/吊图十连` | 随机抽最多 10 张不重复的吊图，以合并转发消息发送 | 180 秒 |
| `/添加吊图` | 回复一张图片后发送本指令，把该图片加入图库 | 无 |

冷却按「用户 + 指令」单独计算，单抽和十连互不影响。

## 配置

在 WebUI 的「插件管理 → 吊图放送 → 配置」里修改，修改后会自动写入 `data/config/astrbot_plugin_funnypic_config.json`。

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `single_pic_cd` | int | `10` | 单张吊图的冷却时间（秒），`0` 表示不限制 |
| `ten_pics_cd` | int | `180` | 十连抽的冷却时间（秒），`0` 表示不限制 |

## 它是怎么工作的

### 图库放在哪

图库固定在 AstrBot 数据目录下，由数据目录加上插件名拼出来，跟进程的工作目录无关：

```python
from pathlib import Path

from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

self.image_dir = Path(get_astrbot_plugin_data_path()) / self.name
```

如果图库路径写成相对路径字符串（`"data/plugin_data/..."`），实际落到哪个目录就取决于启动 AstrBot 时的工作目录，换个启动方式（systemd、Docker、桌面端）就可能读不到原来攒的图。

支持的后缀：`jpg` / `jpeg` / `png` / `gif` / `bmp` / `webp`。

### 十连抽为什么用合并转发

`Node` + `Nodes` 是 OneBot v11 的合并转发消息段，十个 `Node` 打包成一条消息发出去，在群里只占一条消息的位置，也不会把聊天记录刷满：

```python
forward_nodes = [
    Comp.Node(
        uin=bot_self_id,
        name=FORWARD_NODE_SENDER_NAME,
        content=[Comp.Image.fromFileSystem(str(path))],
    )
    for path in random.sample(image_files, num_to_draw)
]
yield event.chain_result([Comp.Nodes(forward_nodes)])
```

合并转发节点的图片会被编码成 `base64://` 内联进 OneBot 报文，所以 NapCat 就算跑在另一台机器上也能正常渲染。

### 投稿是怎么拿到图片的

平台适配器在把 OneBot 消息转成 AstrBot 消息时，就已经为引用消息调过一次 `get_msg`，并把被引用消息的消息段填进了 `Reply.chain`。插件直接读这个字段就行，不用自己再去调协议端的 `get_msg` / `get_image`，也就不用绑死在某一个适配器上：

```python
reply_seg = next(
    (seg for seg in event.get_messages() if isinstance(seg, Comp.Reply)), None
)
quoted_image = next(
    (seg for seg in (reply_seg.chain or []) if isinstance(seg, Comp.Image)), None
)
```

拿到图片段后调用 `await quoted_image.convert_to_file_path()`，它会把 `http` / `base64` / `file://` 各种形式统一落成本地文件，网络图片还会按真实格式补上正确的后缀。落盘时用图片内容的 sha256 前 16 位当文件名，天然去重：

```python
digest = hashlib.sha256(source_path.read_bytes()).hexdigest()[:16]
dest_path = self.image_dir / f"{digest}{suffix}"
```

直接拿协议端返回的 `file` 值当文件名是有坑的：NapCat 有时会给出 `xxxx.image` 这样的名字，文件虽然存下来了，却永远匹配不上后缀白名单，也就永远抽不到。

## 小贴士

- 懒得投稿的话，把图片直接拷进 `data/plugin_data/astrbot_plugin_funnypic/` 也一样生效。
- 图库里少于 10 张时十连会按实际数量抽，并在后面贴一句说明。
- 群聊里想让机器人理你，记得先 @ 它或者用唤醒前缀，这是 AstrBot 的通用规则。
- 想换插件图标，直接把 `logo.png` 替换成自己的 1:1 图片即可。

## 已知限制

- 十连抽依赖 OneBot v11 的 `send_group_forward_msg` / `send_private_forward_msg`。换成 Telegram、Discord 这类适配器时，单抽和投稿仍然可用，但十连抽的合并转发消息收不到。
- 冷却记录保存在内存里，AstrBot 重启后会清空。
- 图库里后缀不在白名单内的文件（例如手工放进来的 `xxxx.image`）不会被抽到，改个后缀就能用。

## 更新日志

### 1.3.0

- 补上 `metadata.yaml` 与 `_conf_schema.json`，插件信息和冷却时间可在 WebUI 里查看与调整。
- 图库路径改为由 AstrBot 数据目录推导，不再受启动目录影响。
- 投稿改为读取 `Reply.chain`，去掉对 `get_msg` / `get_image` 的依赖，并按内容哈希命名去重。
- 补上 `event.stop_event()` 与统一的异常兜底，指令不再漏给 LLM 链路。

### 1.2.1

- 单抽 / 十连合并转发 / 回复投稿，带指令冷却。

## 许可

[MIT License](LICENSE)，随便玩。
