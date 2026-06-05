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

        text = self._extract_text(data.get("message", raw_message))

        if not text.strip():
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
            ))

    def _extract_text(self, message) -> str:
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            parts = []
            mentioned = False
            for seg in message:
                if isinstance(seg, dict):
                    if seg.get("type") == "at":
                        mentioned = True
                    elif seg.get("type") == "text":
                        parts.append(seg.get("data", {}).get("text", ""))
                elif isinstance(seg, str):
                    parts.append(seg)
            text = "".join(parts)
            if mentioned:
                text = "@鱼 " + text
            return text
        return str(message)

    async def _handle_notice(self, data: dict):
        if data.get("sub_type") == "poke" and str(data.get("target_id")) == self.bot_qq:
            user_id = str(data.get("user_id", ""))
            group_id = str(data.get("group_id", ""))
            if user_id and self._on_message:
                asyncio.create_task(self._on_message(
                    bot_qq=self.bot_qq,
                    user_id=user_id,
                    user_name="",
                    text="[戳一戳]",
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
