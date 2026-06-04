"""
消息处理器 — 接收 OneBot 消息，检索记忆，调用 LLM 生成回复。
支持：多消息拆分、群聊自主判断回复、消息合并、引用回复。
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from server.bot.mood import get_mood
from server.bot.persona import PERSONA_SYSTEM_PROMPT
from server.config import get_instance_config
from server.memory.manager import get_memory_manager
from server.memory.context import ContextManager
from server.search.bing import bing_search, format_search_results, needs_search

logger = logging.getLogger("dudushark.message")

SPLIT_PATTERN = re.compile(r"(?<=[。！？\n])\s*")

LLM_RETRIES = 3
LLM_RETRY_BASE_DELAY = 2.0  # seconds, doubled each retry: 2, 4, 8


def _is_retryable(status: int) -> bool:
    return status in (429, 500, 502, 503, 504)


async def _call_llm(base_url: str, api_key: str, payload: dict, timeout: float = 60) -> str:
    """Call LLM API with exponential backoff retry. Raises on final failure."""
    last_err: str = ""
    for attempt in range(LLM_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    base_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
            if _is_retryable(resp.status_code):
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                raise RuntimeError(f"LLM API 错误 {resp.status_code}: {resp.text[:300]}")
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_err = str(e)
        except RuntimeError:
            raise  # non-retryable HTTP errors

        if attempt < LLM_RETRIES - 1:
            delay = LLM_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(f"LLM 调用失败 (尝试 {attempt+1}/{LLM_RETRIES}): {last_err}，{delay:.0f}s 后重试...")
            await asyncio.sleep(delay)

    raise RuntimeError(f"LLM 调用失败（已重试 {LLM_RETRIES} 次）: {last_err}")

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

    def _append_history(self, user_id: str, role: str, content: str, group_id: str = "", proactive: bool = False):
        key = self._conv_key(user_id, group_id)
        if key not in self._conversations:
            self._conversations[key] = []
        self._conversations[key].append({
            "role": role,
            "content": content,
            "ts": time.time(),
            "proactive": proactive,
        })
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
                # Format ISO date to human-readable: "2026-06-04T17:04:38Z" → "06-04 17:04"
                try:
                    if "T" in date:
                        d, t = date.replace("Z", "").split("T", 1)
                        parts = d.split("-")
                        date = f"{parts[1]}-{parts[2]} {t.split(':')[0]}:{t.split(':')[1]}"
                except Exception:
                    pass
                lines.append(f"- [{date}] {m['text'][:500]}")
            memories_text = "\n".join(lines)

        # 构建消息 — 独立 system 消息提高缓存命中率
        # msg[0]=persona(不变→缓存命中), msg[1]=mood, msg[2]=memories, msg[3+]=history
        mood = get_mood(self.bot_qq)
        mood.update()
        mood_context = mood.system_mood_context()

        messages = [{"role": "system", "content": PERSONA_SYSTEM_PROMPT}]

        if mood_context:
            messages.append({"role": "system", "content": "## 你现在的心情\n" + mood_context})

        if memories_text:
            messages.append({"role": "system", "content": "## 咱对这个人的记忆：\n" + memories_text})

        history = self._get_history(user_id, group_id)
        fit_result = self.ctx.fit_messages(PERSONA_SYSTEM_PROMPT, history)
        # Take history parts from fit_result (skip its system msg since we already have prebuilt ones)
        history_msgs = fit_result[1:] if fit_result and len(fit_result) > 1 else []
        messages.extend(history_msgs)

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

        # 调用 LLM（带重试）
        llm = self.cfg.llm
        payload = {
            "model": llm.model,
            "messages": messages,
            "temperature": mood.llm_temperature(0.85),
            "max_tokens": mood.llm_max_tokens(1024),
        }

        try:
            full_reply = await _call_llm(llm.base_url, llm.api_key, payload)
        except Exception as e:
            logger.error(f"LLM 调用最终失败: {e}")
            return [ReplyPart("啊呜...咱这边信号不太好，等会儿再试试好不好？")]

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

    def has_bot_spoken(self, user_id: str, group_id: str = "") -> bool:
        """Check if Dudu has ever replied in this conversation."""
        key = self._conv_key(user_id, group_id)
        return any(m.get("role") == "assistant" for m in self._conversations.get(key, []))

    def get_eligible_conversations(self) -> list[tuple[str, str, str, float]]:
        """Return (conv_key, user_id, group_id, last_ts) for convos where Dudu has spoken."""
        results = []
        for key, msgs in self._conversations.items():
            if not any(m.get("role") == "assistant" for m in msgs):
                continue
            last_ts = max((m.get("ts", 0) for m in msgs), default=0)
            parts = key.split(":")
            user_id = parts[0]
            group_id = parts[1] if len(parts) > 1 else ""
            results.append((key, user_id, group_id, last_ts))
        return results

    async def proactive_message(self, user_id: str, group_id: str = "") -> str | None:
        """Generate a proactive message. Returns text or None if SKIP/error."""
        from server.bot.persona import PERSONA_SYSTEM_PROMPT
        from server.bot.proactive import PROACTIVE_PROMPT

        history = self._get_history(user_id, group_id, max_len=20)
        is_group = bool(group_id)

        context_lines = []
        for m in history:
            role_label = "对方" if m.get("role") == "user" else "咱"
            context_lines.append(f"{role_label}: {m.get('content', '')[:200]}")
        context = "\n".join(context_lines) if context_lines else "（这是第一次和这个人说话）"

        memories = self.memory.recall_by_vector(user_id, "最近过得怎么样 聊天", n=5)
        memories_text = ""
        if memories:
            lines = []
            for m in memories:
                lines.append(f"- [{m.get('meta', {}).get('date', '?')}] {m['text'][:200]}")
            memories_text = "\n".join(lines)

        prompt_text = PROACTIVE_PROMPT.format(context=context)

        mood = get_mood(self.bot_qq)
        mood.update()
        mood_context = mood.system_mood_context()
        system_content = PERSONA_SYSTEM_PROMPT
        if mood_context:
            system_content += "\n\n## 你现在的心情\n" + mood_context
        if memories_text:
            system_content += f"\n\n## 关于这个人的记忆\n{memories_text}"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt_text},
        ]

        llm = self.cfg.llm
        payload = {"model": llm.model, "messages": messages, "temperature": mood.llm_temperature(0.9), "max_tokens": mood.llm_max_tokens(512)}

        try:
            text = await _call_llm(llm.base_url, llm.api_key, payload, timeout=45)
        except Exception as e:
            logger.error(f"Proactive LLM 最终失败: {e}")
            return None

        if not text or text.strip() == "[SKIP]":
            return None

        async with self._lock:
            key = self._conv_key(user_id, group_id)
            if key not in self._conversations:
                self._conversations[key] = []
            self._conversations[key].append({
                "role": "assistant",
                "content": text,
                "ts": time.time(),
                "proactive": True,
            })

        return text


_message_handlers: dict[str, MessageHandler] = {}


def get_message_handler(bot_qq: str) -> MessageHandler:
    if bot_qq not in _message_handlers:
        _message_handlers[bot_qq] = MessageHandler(bot_qq)
    return _message_handlers[bot_qq]
