"""
OneBot v11 协议处理器 — 接收 NapCatQQ 的 WebSocket 消息并分发。
支持正向和反向 WebSocket。
"""

import asyncio
import json
import time
import logging
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger("dudushark.onebot")


class OneBotClient:
    """管理与单个 NapCatQQ 实例的 WebSocket 连接。"""

    def __init__(self, bot_qq: str, on_message=None, on_connect=None, on_disconnect=None):
        self.bot_qq = bot_qq
        self.ws: Optional[WebSocket] = None
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._api_callbacks: dict[str, asyncio.Future] = {}
        self._echo_seq = 0
        self.connected = False
        self.qr_data: Optional[str] = None

    async def handle_ws(self, ws: WebSocket):
        """处理 NapCatQQ 反向 WebSocket 连接。"""
        await ws.accept()
        self.ws = ws
        self.connected = True
        logger.info(f"[{self.bot_qq}] NapCat WebSocket 已连接")
        if self._on_connect:
            await self._on_connect(self.bot_qq)

        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._dispatch(data)
        except Exception as e:
            logger.warning(f"[{self.bot_qq}] WebSocket 断开: {e}")
        finally:
            self.connected = False
            if self._on_disconnect:
                await self._on_disconnect(self.bot_qq)

    async def _dispatch(self, data: dict):
        """分发 OneBot 事件。"""
        post_type = data.get("post_type", data.get("type", ""))

        if post_type == "message" or post_type == "message_sent":
            await self._handle_message(data)
        elif post_type == "notice":
            await self._handle_notice(data)
        elif post_type == "request":
            await self._handle_request(data)
        elif post_type == "meta_event":
            await self._handle_meta(data)
        elif "echo" in data:
            self._handle_api_response(data)

    async def _handle_message(self, data: dict):
        msg_type = data.get("message_type", "private")
        sender = data.get("sender", {})
        user_id = str(sender.get("user_id", data.get("user_id", "")))
        user_name = sender.get("nickname", sender.get("card", ""))
        raw_message = data.get("raw_message", data.get("message", ""))
        group_id = str(data.get("group_id", ""))
        message_id = str(data.get("message_id", ""))

        text, images, image_infos = self._extract_text(data.get("message", raw_message))
        msg_obj = data.get("message")
        if isinstance(msg_obj, list) and any(isinstance(s, dict) and s.get("type") == "reply" for s in msg_obj):
            logger.info(f"[{self.bot_qq}] reply segment found: {msg_obj}")

        if not text.strip() and not images:
            return

        if self._on_message:
            asyncio.create_task(self._on_message(
                bot_qq=self.bot_qq,
                user_id=user_id,
                user_name=user_name,
                text=text,
                group_id=group_id,
                msg_type=msg_type,
                message_id=message_id,
                raw=data,
                images=images,
                image_infos=image_infos,
            ))

    def _extract_text(self, message) -> tuple[str, list[str], list[dict]]:
        """返回 (文本, 图片URL列表, 图片信息列表)。"""
        if isinstance(message, str):
            return message, [], []
        if isinstance(message, list):
            parts = []
            images = []
            image_infos = []  # [{"url":..., "is_sticker":bool, "summary":str}]
            mentioned = False
            has_reply = False
            for seg in message:
                if isinstance(seg, dict):
                    t = seg.get("type", "")
                    if t == "at":
                        mentioned = True
                    elif t == "reply":
                        has_reply = True
                    elif t == "text":
                        parts.append(seg.get("data", {}).get("text", ""))
                    elif t == "image":
                        img_data = seg.get("data", {})
                        url = img_data.get("url", "")
                        sub = img_data.get("sub_type", img_data.get("subType", -1))
                        is_sticker = sub in (1, "1")
                        summary = img_data.get("summary", "").strip()
                        if url:
                            images.append(url)
                            image_infos.append({"url": url, "is_sticker": is_sticker, "summary": summary})
                        if is_sticker:
                            label = f"[表情包：{summary}]" if summary else "[表情包]"
                        else:
                            label = "[图片]"
                        parts.append(label)
                    elif t == "record":
                        rec_data = seg.get("data", {})
                        rec_file = rec_data.get("file", rec_data.get("file_id", ""))
                        rec_url = rec_data.get("url", "")
                        logger.info(f"[{self.bot_qq}] voice record: file={rec_file[:50]}, url={rec_url[:80] if rec_url else 'N/A'}")
                        image_infos.append({"is_voice": True, "file": rec_file, "url": rec_url})
                        parts.append("[语音]")
                    else:
                        logger.info(f"[{self.bot_qq}] unknown seg type: {t}, keys={list(seg.get('data', {}).keys())}")
                elif isinstance(seg, str):
                    parts.append(seg)
            text = "".join(parts)
            if not has_reply and isinstance(text, str) and text.startswith("[CQ:reply"):
                has_reply = True
                import re as _re
                text = _re.sub(r"\[CQ:reply[^\]]*\]", "", text).strip()
            if mentioned:
                text = "@鱼 " + text
            elif has_reply:
                text = "[回复鱼] " + text
            return text, images, image_infos
        return str(message), []

    async def _handle_notice(self, data: dict):
        logger.info(f"[{self.bot_qq}] notice: {data}")
        sub_type = data.get("sub_type", "")
        if sub_type == "poke":
            target = str(data.get("target_id", ""))
            logger.info(f"[{self.bot_qq}] poke: target={target} bot={self.bot_qq}")
            if target == self.bot_qq:
                user_id = str(data.get("user_id", ""))
                group_id = str(data.get("group_id", ""))
                logger.info(f"[{self.bot_qq}] poke matched! user={user_id} group={group_id}")
                if user_id and self._on_message:
                    asyncio.create_task(self._on_message(
                        bot_qq=self.bot_qq,
                        user_id=user_id,
                        user_name=data.get("sender", {}).get("nickname", "") or f"QQ{user_id}",
                        text="刚刚戳了戳鱼",
                        group_id=group_id,
                        msg_type="group",
                        message_id="",
                        raw=data,
                    ))

    async def _handle_request(self, data: dict):
        pass

    async def _handle_meta(self, data: dict):
        meta_type = data.get("meta_event_type", "")
        if meta_type == "heartbeat":
            pass
        elif meta_type == "lifecycle":
            sub = data.get("sub_type", "")
            logger.info(f"[{self.bot_qq}] 生命周期事件: {sub}")

    def _handle_api_response(self, data: dict):
        echo = data.get("echo", "")
        if echo in self._api_callbacks:
            fut = self._api_callbacks.pop(echo)
            if not fut.done():
                fut.set_result(data)

    # ---- API 调用 ----

    async def call_api(self, action: str, params: dict = None, timeout: float = 30) -> dict:
        """调用 OneBot API。"""
        if not self.ws or not self.connected:
            raise RuntimeError("WebSocket 未连接")
        self._echo_seq += 1
        echo = f"api_{self._echo_seq}_{int(time.time())}"
        payload = {"action": action, "params": params or {}, "echo": echo}
        fut = asyncio.get_event_loop().create_future()
        self._api_callbacks[echo] = fut
        try:
            await self.ws.send_text(json.dumps(payload, ensure_ascii=False))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._api_callbacks.pop(echo, None)
            raise

    async def send_private_voice(self, user_id: str, file_path: str) -> dict:
        """发送私聊语音。file_path 为 NapCat 容器内可访问的音频文件路径。"""
        return await self.call_api("send_private_msg", {
            "user_id": int(user_id),
            "message": [{"type": "record", "data": {"file": file_path}}],
        })

    async def send_group_voice(self, group_id: str, file_path: str) -> dict:
        """发送群聊语音。"""
        return await self.call_api("send_group_msg", {
            "group_id": int(group_id),
            "message": [{"type": "record", "data": {"file": file_path}}],
        })

    async def send_private_msg(self, user_id: str, message: str) -> dict:
        return await self.call_api("send_private_msg", {
            "user_id": int(user_id),
            "message": message,
        })

    async def send_group_msg(self, group_id: str, message: str) -> dict:
        return await self.call_api("send_group_msg", {
            "group_id": int(group_id),
            "message": message,
        })

    async def send_private_msg_quote(self, user_id: str, message: str, quote_msg_id: str) -> dict:
        """引用回复私聊消息。"""
        return await self.call_api("send_private_msg", {
            "user_id": int(user_id),
            "message": [
                {"type": "reply", "data": {"id": quote_msg_id}},
                {"type": "text", "data": {"text": message}},
            ],
        })

    async def send_group_msg_quote(self, group_id: str, message: str, quote_msg_id: str) -> dict:
        """引用回复群聊消息。"""
        return await self.call_api("send_group_msg", {
            "group_id": int(group_id),
            "message": [
                {"type": "reply", "data": {"id": quote_msg_id}},
                {"type": "text", "data": {"text": message}},
            ],
        })

    async def get_record(self, file_id: str = "", file: str = "", out_format: str = "wav") -> dict:
        """获取语音文件。file_id 或 file 二选一。"""
        params = {"out_format": out_format}
        if file_id:
            params["file_id"] = file_id
        elif file:
            params["file"] = file
        return await self.call_api("get_record", params)

    async def get_file(self, file_id: str = "", file: str = "") -> dict:
        """获取文件（含 base64）。file_id 或 file 二选一。"""
        params = {}
        if file_id:
            params["file_id"] = file_id
        elif file:
            params["file"] = file
        return await self.call_api("get_file", params)

    async def get_login_info(self) -> dict:
        return await self.call_api("get_login_info")

    async def get_stranger_info(self, user_id: str) -> dict:
        return await self.call_api("get_stranger_info", {"user_id": int(user_id)})


class OneBotServer:
    """管理所有 OneBot 客户端实例。"""

    def __init__(self):
        self.clients: dict[str, OneBotClient] = {}

    def create_client(self, bot_qq: str, **kwargs) -> OneBotClient:
        client = OneBotClient(bot_qq, **kwargs)
        self.clients[bot_qq] = client
        return client

    def remove_client(self, bot_qq: str):
        self.clients.pop(bot_qq, None)

    def get_client(self, bot_qq: str) -> Optional[OneBotClient]:
        return self.clients.get(bot_qq)

    def list_clients(self) -> list[str]:
        return list(self.clients.keys())


onebot_server = OneBotServer()
