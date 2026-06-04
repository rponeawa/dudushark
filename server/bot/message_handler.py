"""
消息处理器 — 接收 OneBot 消息，检索记忆，调用 LLM 生成回复。
支持：多消息拆分、群聊自主判断回复、消息合并、引用回复。
"""

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timezone

import httpx

from server.bot.persona import PERSONA_SYSTEM_PROMPT, FALLBACK_RESPONSES
from server.config import get_instance_config
from server.memory.manager import get_memory_manager
from server.memory.context import ContextManager
from server.search.bing import bing_search, format_search_results, needs_search

logger = logging.getLogger("dudushark.message")

SPLIT_PATTERN = re.compile(r"(?<=[。！？\n])\s*")

PRIVATE_MAX_WINDOW = 16.0   # 私聊最大累计等待
GROUP_MAX_WINDOW = 24.0     # 群聊最大累计等待


class ReplyPart:
    """一条回复，含可选的引用消息 ID。"""
    def __init__(self, text: str, quote_msg_id: str | None = None):
        self.text = text
        self.quote_msg_id = quote_msg_id

    def __repr__(self):
        return f"ReplyPart(text={self.text[:40]!r}, quote={self.quote_msg_id})"


class MessageHandler:
    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self.cfg = get_instance_config(bot_qq)
        self.memory = get_memory_manager(bot_qq)
        self.ctx = ContextManager(max_tokens=self.cfg.context_max_tokens)
        self._conversations: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()
        # 缓冲：(conv_key, user_name) -> {"texts": [...], "msg_ids": [...], "first_ts": float, "futures": [Future]}
        self._buffers: dict[tuple[str, str], dict] = {}

    def _conv_key(self, user_id: str, group_id: str = "") -> str:
        return f"{user_id}:{group_id}" if group_id else user_id

    def _get_history(self, user_id: str, group_id: str = "", max_len: int = 40) -> list[dict]:
        key = self._conv_key(user_id, group_id)
        return self._conversations.get(key, [])[-max_len:]

    def _append_history(self, user_id: str, role: str, content: str, group_id: str = ""):
        key = self._conv_key(user_id, group_id)
        if key not in self._conversations:
            self._conversations[key] = []
        self._conversations[key].append({"role": role, "content": content})
        if len(self._conversations[key]) > 200:
            self._conversations[key] = self._conversations[key][-100:]

    def _split_reply(self, text: str) -> list[str]:
        if not self.cfg.reply_split_enabled:
            return [text]
        text = text.strip()
        if len(text) <= 200:
            return [text]
        parts = []
        paragraphs = [p.strip() for p in SPLIT_PATTERN.split(text) if p.strip()]
        current = ""
        for para in paragraphs:
            if len(current) + len(para) > 250 and current:
                parts.append(current.strip())
                current = para
            else:
                current += para
        if current.strip():
            parts.append(current.strip())
        if len(parts) == 1 and len(parts[0]) > 300:
            long_text = parts[0]
            parts = []
            for i in range(0, len(long_text), 250):
                parts.append(long_text[i : i + 250])
        return parts[: self.cfg.reply_split_max]

    async def handle(
        self, user_id: str, user_name: str, text: str,
        group_id: str = "", msg_type: str = "private", message_id: str = ""
    ) -> list[ReplyPart]:
        """统一入口。返回 ReplyPart 列表，每个可带引用消息 ID。"""
        is_group = bool(group_id)
        conv_key = self._conv_key(user_id, group_id)
        buf_key = (conv_key, user_name)
        merge_delay = self.cfg.group_merge_delay if is_group else self.cfg.private_merge_delay
        max_window = (GROUP_MAX_WINDOW if is_group else PRIVATE_MAX_WINDOW)
        now = time.time()

        existing = self._buffers.get(buf_key)
        if existing and (now - existing["first_ts"]) < max_window:
            existing["texts"].append(text)
            existing["msg_ids"].append(message_id)
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            existing["futures"].append(fut)
            if existing.get("task") and not existing["task"].done():
                existing["task"].cancel()
            existing["task"] = asyncio.create_task(
                self._flush_and_resolve(buf_key, group_id, user_id, user_name, is_group, merge_delay)
            )
            return await fut
        else:
            fut: asyncio.Future = asyncio.get_event_loop().create_future()
            self._buffers[buf_key] = {
                "texts": [text],
                "msg_ids": [message_id],
                "first_ts": now,
                "futures": [fut],
                "task": asyncio.create_task(
                    self._flush_and_resolve(buf_key, group_id, user_id, user_name, is_group, merge_delay)
                ),
            }
            return await fut

    async def _flush_and_resolve(self, buf_key, group_id, user_id, user_name, is_group, delay):
        await asyncio.sleep(delay)
        buf = self._buffers.pop(buf_key, None)
        futures = buf.get("futures", []) if buf else []

        if not buf or not buf["texts"]:
            for f in futures:
                if not f.done():
                    f.set_result([])
            return

        texts = buf["texts"]
        msg_ids = buf.get("msg_ids", [])
        combined = "\n".join(f"{user_name}: {t}" for t in texts) if len(texts) > 1 else texts[0]
        # 引用时指向最后一条消息
        last_msg_id = msg_ids[-1] if msg_ids else ""

        async with self._lock:
            replies = await self._handle_impl(
                user_id, user_name, combined, group_id, "group" if is_group else "private", last_msg_id
            )

        for i, f in enumerate(futures):
            if not f.done():
                f.set_result(replies if i == 0 else [])

    async def _handle_impl(
        self, user_id: str, user_name: str, text: str, group_id: str = "",
        msg_type: str = "private", quote_msg_id: str = ""
    ) -> list[ReplyPart]:
        is_group = bool(group_id)

        # 检索记忆
        memories = self.memory.recall_by_vector(user_id, text, n=self.cfg.memory_retrieval_count)
        memories_text = ""
        if memories:
            lines = []
            for m in memories:
                date = m.get("meta", {}).get("date", "未知")
                lines.append(f"- [{date}] {m['text'][:500]}")
            memories_text = "\n".join(lines)

        # 构建消息
        history = self._get_history(user_id, group_id)
        messages = self.ctx.fit_messages(PERSONA_SYSTEM_PROMPT, history, memories_text)

        prefix = "[群聊]" if is_group else ""
        user_msg = {"role": "user", "content": f"{prefix}{user_name} 说: {text}"}
        messages.append(user_msg)
        self._append_history(user_id, "user", text, group_id)

        # 网络搜索
        if self.cfg.web_search_enabled and needs_search(text):
            try:
                results = await bing_search(text)
                if results:
                    search_context = (
                        "\n\n## 网络搜索结果（咱偷偷去查的，不要直接贴搜索结果哦，自然地用咱的语气说出来）：\n"
                        + format_search_results(results)
                    )
                    messages.append({"role": "system", "content": search_context})
            except Exception:
                pass

        # 调用 LLM
        llm = self.cfg.llm
        headers = {
            "Authorization": f"Bearer {llm.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": llm.model,
            "messages": messages,
            "temperature": 0.85,
            "max_tokens": 1024,
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(llm.base_url, headers=headers, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    full_reply = data["choices"][0]["message"]["content"].strip()
                else:
                    logger.warning(f"LLM API 错误 {resp.status_code}: {resp.text[:300]}")
                    full_reply = random.choice(FALLBACK_RESPONSES)
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            full_reply = random.choice(FALLBACK_RESPONSES)

        if full_reply.strip() == "[SKIP]":
            return []

        # 解析引用标记：LLM 在句首写 >> 表示想引用回复
        want_quote = False
        if full_reply.startswith(">>"):
            want_quote = True
            full_reply = full_reply[2:].strip()

        parts = self._split_reply(full_reply)
        result = []
        for i, part in enumerate(parts):
            # 只有第一段带引用（避免多段每条都引用）
            qid = quote_msg_id if (want_quote and i == 0 and quote_msg_id) else None
            result.append(ReplyPart(part, qid))
            self._append_history(user_id, "assistant", part, group_id)

        try:
            self.memory.auto_remember_from_message(user_id, user_name, text, full_reply)
        except Exception:
            pass

        return result

    def reload_config(self):
        self.cfg = get_instance_config(self.bot_qq)
        self.ctx = ContextManager(max_tokens=self.cfg.context_max_tokens)

    def get_conversation(self, user_id: str, group_id: str = "") -> list[dict]:
        return self._get_history(user_id, group_id)

    def clear_conversation(self, user_id: str, group_id: str = ""):
        key = self._conv_key(user_id, group_id)
        self._conversations.pop(key, None)

    def list_conversations(self) -> list[str]:
        return list(self._conversations.keys())


_message_handlers: dict[str, MessageHandler] = {}


def get_message_handler(bot_qq: str) -> MessageHandler:
    if bot_qq not in _message_handlers:
        _message_handlers[bot_qq] = MessageHandler(bot_qq)
    return _message_handlers[bot_qq]
