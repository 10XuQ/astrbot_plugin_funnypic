"""AstrBot 吊图插件 (FunnyPic).

从本地图库随机抽取「吊图」发给用户，并支持用户回复图片来投稿。

设计要点：
- 图库固定在 AstrBot 数据目录下的 ``data/plugin_data/astrbot_plugin_funnypic``，
  不随进程的工作目录变化。
- 十连抽以 OneBot v11 合并转发消息发送，一次十条也不会刷屏。
- 投稿直接读取引用消息里的图片段（``Reply.chain``），插件不再自己去调用
  ``get_msg`` / ``get_image`` 这类协议端专有接口。
- 全文件不使用 emoji：部分客户端（如 QQ/NapCat）的字体缺少 emoji 字形，
  会把 emoji 渲染成豆腐块，因此统一改用纯文字与 CJK 标点。
"""

import hashlib
import math
import random
import shutil
import time
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

# 插件目录名，同时也是 metadata.yaml 里的 name。
PLUGIN_DIR_NAME = "astrbot_plugin_funnypic"

# 允许进入图库的图片后缀。更早的版本会把协议端返回的文件名原样保存，
# 像 "xxxx.image" 这种没有标准后缀的文件因为不在白名单里，永远不会被抽到。
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# 合并转发消息里显示的发送者昵称。
FORWARD_NODE_SENDER_NAME = "吊图放送"

# 十连抽最多抽多少张。
TEN_PICS_COUNT = 10

# 冷却时间的默认值（秒），可在 WebUI 中通过插件配置修改。
DEFAULT_SINGLE_PIC_CD = 10
DEFAULT_TEN_PICS_CD = 180


class FunnyPicPlugin(Star):
    """吊图插件：随机单抽、十连抽与用户投稿。

    指令：
      /吊图        随机抽一张吊图（默认 10 秒冷却）
      /吊图十连    随机抽最多十张吊图，以合并转发消息发送（默认 180 秒冷却）
      /添加吊图    回复一张图片并发送本指令，即可把图片加入图库

    所有指令都不需要加 / 前缀，直接发送命令名也可以。
    图库位置：data/plugin_data/astrbot_plugin_funnypic
    """

    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        """初始化插件。

        Args:
            context: AstrBot 传入的插件上下文。
            config: 由 ``_conf_schema.json`` 解析出的插件配置，可能为 None。
        """
        super().__init__(context)
        self.config = config or {}

        # self.name 由 AstrBot 从 metadata.yaml 注入，等于插件目录名，
        # 所以这里不用再手写一遍图库目录名。旧版本用的是相对路径字符串
        # "data/plugin_data/astrbot_plugin_funnypic"，落到哪个目录取决于
        # 进程的工作目录，这里改成从数据目录显式解析。
        self.image_dir = Path(get_astrbot_plugin_data_path()) / (
            self.name or PLUGIN_DIR_NAME
        )
        try:
            self.image_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.error(f"funnypic: 创建图库目录 {self.image_dir} 失败: {exc}")

        self.single_pic_cd = max(
            0, int(self.config.get("single_pic_cd", DEFAULT_SINGLE_PIC_CD))
        )
        self.ten_pics_cd = max(
            0, int(self.config.get("ten_pics_cd", DEFAULT_TEN_PICS_CD))
        )

        # 冷却记录只放在内存里：{用户 ID: {指令类型: 上次成功使用的时间戳}}。
        self.user_command_timestamps: dict[str, dict[str, float]] = {}

    def _get_image_list(self) -> list[Path]:
        """列出图库里所有受支持的图片文件。

        Returns:
            按文件名排序的图片路径列表；目录缺失或读取失败时返回空列表。
        """
        try:
            return sorted(
                path
                for path in self.image_dir.iterdir()
                if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
            )
        except OSError as exc:
            logger.error(f"funnypic: 读取图库目录 {self.image_dir} 失败: {exc}")
            return []

    def _check_cd(
        self, user_id: str, command_type: str, cd_seconds: int
    ) -> tuple[bool, int]:
        """检查某个用户是否还在指令冷却中。

        Args:
            user_id: 发送者的用户 ID。
            command_type: 指令类型，用于区分单抽和十连各自的冷却。
            cd_seconds: 冷却时长（秒），小于等于 0 表示不限制。

        Returns:
            二元组 ``(是否允许执行, 剩余冷却秒数)``。
        """
        if cd_seconds <= 0:
            return True, 0
        last_time = self.user_command_timestamps.get(user_id, {}).get(command_type, 0.0)
        remaining = cd_seconds - (time.time() - last_time)
        if remaining > 0:
            return False, math.ceil(remaining)
        return True, 0

    def _update_timestamp(self, user_id: str, command_type: str) -> None:
        """记录用户本次成功使用指令的时间。

        Args:
            user_id: 发送者的用户 ID。
            command_type: 指令类型。
        """
        self.user_command_timestamps.setdefault(user_id, {})[command_type] = time.time()

    @filter.command("吊图", alias={"/吊图"})
    async def get_single_pic(self, event: AstrMessageEvent):
        """随机抽取一张吊图。

        Args:
            event: 当前消息事件。

        Yields:
            一张图片，或说明为什么没有图片的提示文本。
        """
        try:
            sender_id = event.get_sender_id()
            allowed, remaining = self._check_cd(sender_id, "single", self.single_pic_cd)
            if not allowed:
                yield event.plain_result(f"别急，冷却时间还有 {remaining} 秒哦！")
            elif not (image_files := self._get_image_list()):
                yield event.plain_result(
                    "图库是空的哦，快用 '添加吊图' 功能来丰富它吧！"
                )
            else:
                yield event.image_result(str(random.choice(image_files)))
                self._update_timestamp(sender_id, "single")
        except Exception as exc:  # noqa: BLE001 - 兜底，避免单次异常影响插件整体
            logger.error(f"funnypic: 抽取单张吊图失败: {exc}", exc_info=True)
            yield event.plain_result("抽图的时候出了点小问题，等会儿再试试吧~")

        # 阻止事件继续传播，避免指令再被 LLM 等后续流程处理一遍。
        event.stop_event()

    @filter.command("吊图十连", alias={"/吊图十连"})
    async def get_ten_pics(self, event: AstrMessageEvent):
        """随机抽取最多十张不重复的吊图，以合并转发消息发送。

        Args:
            event: 当前消息事件。

        Yields:
            一条合并转发消息；图库不足十张时会再补一句说明。
        """
        try:
            sender_id = event.get_sender_id()
            allowed, remaining = self._check_cd(sender_id, "ten", self.ten_pics_cd)
            if not allowed:
                yield event.plain_result(f"别急，冷却时间还有 {remaining} 秒哦！")
            elif not (image_files := self._get_image_list()):
                yield event.plain_result("图库是空的哦，一张都抽不出来！")
            else:
                # 图库不足十张时按实际数量抽，sample 保证同一批里不会重复。
                num_to_draw = min(len(image_files), TEN_PICS_COUNT)
                bot_self_id = event.get_self_id()
                forward_nodes = [
                    Comp.Node(
                        uin=bot_self_id,
                        name=FORWARD_NODE_SENDER_NAME,
                        content=[Comp.Image.fromFileSystem(str(path))],
                    )
                    for path in random.sample(image_files, num_to_draw)
                ]
                yield event.chain_result([Comp.Nodes(forward_nodes)])
                if num_to_draw < TEN_PICS_COUNT:
                    yield event.plain_result(
                        f"（图库里只有{num_to_draw}张图，就先抽这么多了~）"
                    )
                self._update_timestamp(sender_id, "ten")
        except Exception as exc:  # noqa: BLE001 - 兜底，避免单次异常影响插件整体
            logger.error(f"funnypic: 抽取十张吊图失败: {exc}", exc_info=True)
            yield event.plain_result("抽图的时候出了点小问题，等会儿再试试吧~")

        # 阻止事件继续传播，避免指令再被 LLM 等后续流程处理一遍。
        event.stop_event()

    @filter.command("添加吊图", alias={"/添加吊图"})
    async def add_pic(self, event: AstrMessageEvent):
        """把引用消息里的图片加入本地图库。

        Args:
            event: 当前消息事件，需要是一条回复了图片的消息。

        Yields:
            一条说明投稿结果的提示文本。
        """
        try:
            # 引用消息里的图片段由平台适配器在解析消息时就填好了，
            # 插件直接读 chain 即可，不用自己调协议端接口。
            reply_seg = next(
                (seg for seg in event.get_messages() if isinstance(seg, Comp.Reply)),
                None,
            )
            if reply_seg is None:
                yield event.plain_result("使用这个功能需要回复(引用)一张图片哦！")
            else:
                quoted_image = next(
                    (
                        seg
                        for seg in (reply_seg.chain or [])
                        if isinstance(seg, Comp.Image)
                    ),
                    None,
                )
                if quoted_image is None:
                    yield event.plain_result("引用的消息里没有找到图片哦~")
                else:
                    # convert_to_file_path() 会把 http / base64 / file:// 统一落成本地
                    # 文件，网络图片还会按真实格式补上正确的后缀。
                    source_path = Path(await quoted_image.convert_to_file_path())
                    # 用图片内容的哈希做文件名：天然去重，也不依赖协议端返回的
                    # 文件名格式（旧版本直接用对端的 file 值，没有后缀的图存进来
                    # 就再也不会被抽到了）。
                    digest = hashlib.sha256(source_path.read_bytes()).hexdigest()[:16]
                    suffix = source_path.suffix.lower()
                    if suffix not in SUPPORTED_SUFFIXES:
                        suffix = ".jpg"
                    dest_path = self.image_dir / f"{digest}{suffix}"

                    if dest_path.exists():
                        yield event.plain_result("这张图我好像已经有了哦。")
                    else:
                        shutil.copyfile(source_path, dest_path)
                        logger.info(f"funnypic: 新增吊图 {dest_path.name}")
                        yield event.plain_result("好耶！新的吊图已添加！")
        except Exception as exc:  # noqa: BLE001 - 兜底，避免单次异常影响插件整体
            logger.error(f"funnypic: 保存用户投稿的图片失败: {exc}", exc_info=True)
            yield event.plain_result(
                "哎呀，图片保存失败了，可能是我的小仓库出了点问题。"
            )

        # 阻止事件继续传播，避免指令再被 LLM 等后续流程处理一遍。
        event.stop_event()

    async def terminate(self):
        """插件被禁用或重载时调用，清空内存中的冷却记录。"""
        self.user_command_timestamps.clear()
