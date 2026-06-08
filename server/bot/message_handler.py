"""
消息处理器 — 接收 OneBot 消息，检索记忆，调用 LLM 生成回复。
支持：多消息拆分、群聊自主判断回复、消息合并、引用回复。
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx

from server.bot.mood import get_mood
from server.bot.persona import PERSONA_SYSTEM_PROMPT
from server.config import get_instance_config
from server.memory.manager import get_memory_manager
from server.memory.context import ContextManager

# 敏感词过滤：避免触发 StepFun 审查。匹配到的内容替换为无害占位符
_SENSITIVE_PATTERNS = [
    (r"(?<!\d)451(?!\d)", "[已过滤]"),
    (r"(?<![a-zA-Z])gay(?![a-zA-Z])", "[已过滤]"),
    (r"censorship[_\. ]?blocked", "[已过滤]"),
    (r"审查拦截", "[已过滤]"),
    (r"(?<![a-zA-Z])a-?17(?![a-zA-Z\d])", "[已过滤]"),
    (r"谷歌亲儿子", "[已过滤]"),
    (r"原神启动", "[已过滤]"),
]


def _sanitize_for_llm(text: str) -> str:
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text
from server.search.bing import bing_search, format_search_results

logger = logging.getLogger("dudushark.message")

SPLIT_PATTERN = re.compile(r"(?<=[。！？\n～])(?<!啊呜～)\s*")

LLM_RETRIES = 3
LLM_RETRY_BASE_DELAY = 2.0  # seconds, doubled each retry: 2, 4, 8

# 速率限制：每分钟最多 10 次（StepFun API 限额），留 2 次余量给主动消息
_RATE_LIMIT = 8
_RATE_WINDOW = 60.0
_rate_timestamps: list[float] = []
_rate_lock = asyncio.Lock()


async def _acquire_rate():
    """获取一次 LLM 调用配额，必要时等待。"""
    global _rate_timestamps
    async with _rate_lock:
        now = time.time()
        _rate_timestamps = [t for t in _rate_timestamps if now - t < _RATE_WINDOW]
        if len(_rate_timestamps) >= _RATE_LIMIT:
            wait = _rate_timestamps[0] + _RATE_WINDOW - now + 1.0
            if wait > 0:
                logger.warning(f"LLM 速率限制：等待 {wait:.1f}s...")
                await asyncio.sleep(wait)
                now = time.time()
                _rate_timestamps = [t for t in _rate_timestamps if now - t < _RATE_WINDOW]
        _rate_timestamps.append(now)


def _is_retryable(status: int) -> bool:
    return status in (404, 429, 500, 502, 503, 504)


async def _call_llm(base_url: str, api_key: str, payload: dict, timeout: float = 60) -> str:
    """Call LLM API with exponential backoff retry. Returns content string."""
    msg = await _call_llm_msg(base_url, api_key, payload, timeout)
    content = msg.get("content", "").strip()
    if content:
        return content
    # Fallback: model in reasoning mode
    reasoning = msg.get("reasoning", "").strip()
    if reasoning:
        # Try JSON first
        m = re.search(r'\{[^{}]*"reply"[^}]*\}', reasoning)
        if not m:
            m = re.search(r'\{[^{}]*\}', reasoning)
        if m:
            return m.group(0)
        # No JSON — return the last line (typically the intended answer for YES/NO calls)
        lines = [l.strip() for l in reasoning.split("\n") if l.strip()]
        if lines:
            return lines[-1]
    return ""


async def _call_llm_msg(base_url: str, api_key: str, payload: dict, timeout: float = 60) -> dict:
    """Call LLM API with retry. Returns the full message dict (content + tool_calls)."""
    await _acquire_rate()
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
                return resp.json()["choices"][0]["message"]
            if _is_retryable(resp.status_code):
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
            else:
                raise RuntimeError(f"LLM API 错误 {resp.status_code}: {resp.text[:300]}")
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as e:
            last_err = str(e)
        except RuntimeError:
            raise

        if attempt < LLM_RETRIES - 1:
            delay = LLM_RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(f"LLM 调用失败 (尝试 {attempt+1}/{LLM_RETRIES}): {last_err}，{delay:.0f}s 后重试...")
            await asyncio.sleep(delay)

    raise RuntimeError(f"LLM 调用失败（已重试 {LLM_RETRIES} 次）: {last_err}")

PRIVATE_MAX_WINDOW = 60.0   # 私聊最大累计等待
GROUP_MAX_WINDOW = 60.0     # 群聊最大累计等待


class ReplyPart:
    """一条回复，含可选的引用消息 ID、语音标记和情绪。"""
    def __init__(self, text: str, quote_msg_id: str | None = None, voice: bool = False, voice_emotion: str = ""):
        self.text = text
        self.quote_msg_id = quote_msg_id
        self.voice = voice
        self.voice_emotion = voice_emotion

    def __repr__(self):
        v = f" voice({self.voice_emotion})" if self.voice else ""
        return f"ReplyPart(text={self.text[:40]!r}, quote={self.quote_msg_id}{v})"


class MessageHandler:
    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self.cfg = get_instance_config(bot_qq)
        self.memory = get_memory_manager(bot_qq)
        self.ctx = ContextManager(max_tokens=self.cfg.context_max_tokens)
        self._conversations: dict[str, list[dict]] = {}
        self._convo_types: dict[str, str] = {}  # key -> "group" or "private"
        self._paused_groups: set[str] = set(self.cfg.paused_groups or [])  # 被管理员暂停的群
        self._lock = asyncio.Lock()
        self._buffers: dict[tuple[str, str], dict] = {}
        self._last_combined: dict[str, str] = {}
        self._last_relay_ts: float = 0.0
        self._last_relay_hash: str = ""
        self._pending_relays: list[dict] = []
        self._relay_checker_started = False
        self._load_conversations()
        self._load_pending_relays()
        self._ensure_relay_checker()

    def _save_paused_groups(self):
        """持久化暂停列表到 bot_config.json。"""
        try:
            self.cfg.paused_groups = sorted(self._paused_groups)
            from server.config import save_instance_config
            save_instance_config(self.cfg)
        except Exception:
            pass

    def _conv_key(self, user_id: str, group_id: str = "") -> str:
        # 群聊所有用户共享对话历史，私聊各自独立
        return group_id if group_id else user_id

    def _get_history(self, user_id: str, group_id: str = "", max_len: int = 40) -> list[dict]:
        key = self._conv_key(user_id, group_id)
        msgs = self._conversations.get(key, [])[-max_len:]
        return [{**m, "content": _sanitize_for_llm(m.get("content", ""))} for m in msgs]

    def _append_history(self, user_id: str, role: str, content: str, group_id: str = "", proactive: bool = False):
        key = self._conv_key(user_id, group_id)
        if key not in self._conversations:
            self._conversations[key] = []
        self._convo_types[key] = "group" if group_id else "private"
        self._conversations[key].append({
            "role": role,
            "content": content,
            "ts": time.time(),
            "proactive": proactive,
        })
        self._persist_convo(key)

    def _convo_file(self, key: str):
        from server.config import get_convo_dir
        safe = key.replace("/", "_").replace("\\", "_")
        return get_convo_dir(self.bot_qq) / f"{safe}.jsonl"

    def _persist_convo(self, key: str):
        try:
            msgs = self._conversations.get(key, [])
            lines = [json.dumps(m, ensure_ascii=False) for m in msgs]
            self._convo_file(key).write_text("\n".join(lines) + "\n", encoding="utf-8")
        except Exception:
            pass

    def _load_conversations(self):
        from server.config import get_convo_dir
        convo_dir = get_convo_dir(self.bot_qq)
        if not convo_dir.exists():
            return
        for f in convo_dir.glob("*.jsonl"):
            try:
                key = f.stem
                msgs = []
                for line in f.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        m = json.loads(line)
                        m["content"] = _sanitize_for_llm(m.get("content", ""))
                        msgs.append(m)
                if msgs:
                    self._conversations[key] = msgs
                    # 检测对话类型：群聊的合并消息包含多个不同说话人
                    import re as _re
                    is_group = False
                    for m in msgs:
                        if m.get("role") != "user":
                            continue
                        content = m.get("content", "")
                        names = set(_re.findall(r"\[\d+\]\s*([^:]+):", content))
                        if len(names) > 1:
                            is_group = True
                            break
                    self._convo_types[key] = "group" if is_group else "private"
            except Exception:
                pass

    def _split_reply(self, text: str) -> list[str]:
        if not self.cfg.reply_split_enabled:
            return [text]
        text = text.strip()
        parts = [p.strip() for p in SPLIT_PATTERN.split(text) if p.strip()]
        return parts

    async def handle(
        self, user_id: str, user_name: str, text: str,
        group_id: str = "", msg_type: str = "private", message_id: str = "",
        images: list[str] | None = None,
        image_infos: list[dict] | None = None,
    ) -> list[ReplyPart]:
        """统一入口。返回 ReplyPart 列表，每个可带引用消息 ID。"""
        is_group = bool(group_id)
        conv_key = self._conv_key(user_id, group_id)
        # 群聊合并所有说话人，私聊只合并同一人
        buf_key = (conv_key, user_name) if not is_group else (conv_key,)
        merge_delay = self.cfg.group_merge_delay if is_group else self.cfg.private_merge_delay
        max_window = (GROUP_MAX_WINDOW if is_group else PRIVATE_MAX_WINDOW)
        now = time.time()

        existing = self._buffers.get(buf_key)
        if existing and (now - existing["first_ts"]) < max_window:
            existing["texts"].append(text)
            existing["names"].append(user_name)
            existing["user_ids"].append(user_id)
            existing["msg_ids"].append(message_id)
            if images:
                existing.setdefault("all_images", []).extend(images)
            if image_infos:
                existing.setdefault("all_image_infos", []).extend(image_infos)
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
                "names": [user_name],
                "user_ids": [user_id],
                "msg_ids": [message_id],
                "first_ts": now,
                "futures": [fut],
                "all_images": list(images) if images else [],
                "all_image_infos": list(image_infos) if image_infos else [],
                "task": asyncio.create_task(
                    self._flush_and_resolve(buf_key, group_id, user_id, user_name, is_group, merge_delay)
                ),
            }
            return await fut

    async def _flush_and_resolve(self, buf_key, group_id, user_id, user_name, is_group, delay):
        await asyncio.sleep(delay)
        buf = self._buffers.get(buf_key)
        futures = buf.get("futures", []) if buf else []

        if not buf or not buf["texts"]:
            self._buffers.pop(buf_key, None)
            for f in futures:
                if not f.done():
                    f.set_result([])
            return

        texts = buf["texts"]
        names = buf.get("names", [user_name] * len(texts))
        user_ids_batch = buf.get("user_ids", [user_id] * len(texts))
        msg_ids = buf.get("msg_ids", [])
        all_images = buf.get("all_images", [])
        all_image_infos = buf.get("all_image_infos", [])
        logger.info(f"[flush@{buf_key}] {len(texts)}条: {list(zip(names, texts))}")
        combined = "\n".join(f"{n}: {t}" for n, t in zip(names, texts)) if len(texts) > 1 else texts[0]
        # 合并消息用序号标注，让 LLM 知道每条消息的索引
        if len(texts) > 1:
            combined = "\n".join(f"[{i+1}] {n}: {t}" for i, (n, t) in enumerate(zip(names, texts)))
        combined = _sanitize_for_llm(combined)
        last_msg_id = msg_ids[-1] if msg_ids else ""
        # name → user_id 映射，用于 LLM memory 中 user 字段
        names_map = dict(zip(names, user_ids_batch))

        async with self._lock:
            replies = await self._handle_impl(
                user_id, user_name, combined, group_id, "group" if is_group else "private",
                last_msg_id, names_map, all_images, msg_ids, all_image_infos
            )

        # 保存合并全文，供前端事件使用
        conv_key = self._conv_key(user_id, group_id)
        if len(texts) > 1:
            self._last_combined[conv_key] = combined
        # LLM 调用完成后才 pop，避免期间新消息开新 buffer
        self._buffers.pop(buf_key, None)
        for i, f in enumerate(futures):
            if not f.done():
                f.set_result(replies if i == 0 else [])

    async def _handle_impl(
        self, user_id: str, user_name: str, text: str, group_id: str = "",
        msg_type: str = "private", quote_msg_id: str = "",
        names_map: dict[str, str] | None = None,
        images: list[str] | None = None,
        msg_ids: list[str] | None = None,
        image_infos: list[dict] | None = None,
    ) -> list[ReplyPart]:
        is_group = bool(group_id)

        # 检索记忆（对人的 + 全局记忆）
        def _fmt_memories(mems: list[dict]) -> str:
            lines = []
            for m in mems:
                date = m.get("meta", {}).get("date", "未知")
                try:
                    if "T" in date:
                        d, t = date.replace("Z", "").split("T", 1)
                        parts = d.split("-")
                        date = f"{parts[1]}-{parts[2]} {t.split(':')[0]}:{t.split(':')[1]}"
                except Exception:
                    pass
                lines.append(f"- [{date}] {m['text'][:400]}")
            return "\n".join(lines)

        # 个人记忆检索：群聊合并消息时检索所有说话人的记忆
        personal_memories = []
        seen_ids = set()
        if is_group and names_map:
            for uid in names_map.values():
                if uid not in seen_ids:
                    seen_ids.add(uid)
                    personal_memories.extend(
                        self.memory.recall_by_vector(uid, text, n=max(1, self.cfg.memory_retrieval_count // 2))
                    )
            # 去重 + 按分数排序
            personal_memories.sort(key=lambda x: x.get("score", 0), reverse=True)
            personal_memories = personal_memories[:self.cfg.memory_retrieval_count]
        else:
            personal_memories = self.memory.recall_by_vector(user_id, text, n=self.cfg.memory_retrieval_count)
        memories_text = _fmt_memories(personal_memories)
        diary_text = _fmt_memories(
            self.memory.recall_by_vector("__diary__", text, n=4)
        )
        # 群聊记忆
        group_mem_text = ""
        if is_group:
            group_mem_text = _fmt_memories(
                self.memory.recall_by_vector(f"__group__{group_id}", text, n=3)
            )

        # 构建消息 — 独立 system 消息提高缓存命中率
        # msg[0]=persona(不变→缓存命中), msg[1]=mood, msg[2]=diary, msg[3]=group_mem, msg[4]=memories, msg[5+]=history
        mood = get_mood(self.bot_qq)
        mood.update()
        mood_context = mood.system_mood_context()

        # 只有当前发送者是管理员时，才注入管理员描述（防止信息泄露）
        is_sender_admin = any(str(a.get("qq", "")) == user_id for a in self.cfg.admins)

        # 群聊暂停/恢复：被暂停的群直接跳过，不落盘
        if is_group and group_id in self._paused_groups:
            logger.info(f"[{self.bot_qq}] Group {group_id} is paused, checking /resume: admin={is_sender_admin}, text={text[:80]}")
            # 检查是否是管理员发的 /resume（可能带回复/@前缀）
            if is_sender_admin and "/resume" in text:
                self._paused_groups.discard(group_id)
                self._save_paused_groups()
                logger.info(f"[{self.bot_qq}] Group {group_id} resumed by admin {user_id}")
                return [ReplyPart("啊呜～鱼回来啦！有什么好玩的事吗？")]
            # 其他消息全部忽略，不落盘
            return []

        # /pause 命令：管理员暂停群聊（可能带回复/@前缀）
        if is_sender_admin and is_group and "/pause" in text:
            self._paused_groups.add(group_id)
            self._save_paused_groups()
            logger.info(f"[{self.bot_qq}] Group {group_id} paused by admin {user_id}")
            return [ReplyPart("啊呜～鱼先歇会儿...有事叫鱼就好～")]

        # /say 命令：管理员测试语音  (/say [情绪] 文本)
        import re as _re2
        say_m = _re2.search(r'/say\s+(.+)', text)
        if is_sender_admin and say_m:
            raw_say = say_m.group(1).strip()
            # 支持情绪前缀: /say 撒娇 啊呜～
            EMOTIONS = {"撒娇", "高兴", "非常高兴", "悲伤", "生气", "非常生气", "兴奋", "惊讶", "恐惧", "困惑"}
            say_emotion = ""
            say_text = raw_say
            for em in sorted(EMOTIONS, key=len, reverse=True):
                if raw_say.startswith(em + " ") or raw_say == em:
                    say_emotion = em
                    say_text = raw_say[len(em):].strip()
                    break
            if say_text:
                audio = await self._tts_speak(say_text, say_emotion)
                if audio:
                    import os, uuid
                    tts_dir = self.cfg.tts_host_dir or os.path.join(os.path.expanduser("~"), "napcat/config/tts")
                    os.makedirs(tts_dir, exist_ok=True)
                    fname = f"{uuid.uuid4().hex[:8]}.wav"
                    with open(os.path.join(tts_dir, fname), "wb") as f:
                        f.write(audio)
                    docker_path = f"/app/napcat/config/tts/{fname}"
                    from server.bot.onebot_handler import onebot_server as _obs
                    _client = _obs.get_client(self.bot_qq)
                    if _client and _client.connected:
                        if is_group:
                            await _client.send_group_voice(group_id, docker_path)
                        else:
                            await _client.send_private_voice(user_id, docker_path)
                        logger.info(f"[{self.bot_qq}] /say voice sent: {say_text[:50]}")
                    return []
                return [ReplyPart("TTS 失败...啊呜～")]
            return []

        # 管理员发空间：管理员 + 硬关键词"空间/说说/动态"才注入 qzone 字段，主 LLM 自行判断是否要发
        # 合并消息场景：检查任意说话人是否为管理员
        _qzone_admin = is_sender_admin or any(
            any(str(a.get("qq", "")) == uid for a in self.cfg.admins)
            for uid in (names_map.values() if names_map else [])
        )
        _qzone_keyword = _qzone_admin and any(kw in text for kw in ("空间", "说说", "动态"))

        # 检查发送者是否具有"家族成员"角色（role 中含"妈"字的为家人）
        _is_family = any(
            str(a.get("qq", "")) == user_id and "妈" in str(a.get("role", ""))
            for a in self.cfg.admins
        )
        # 群聊合并消息：检查是否包含管理员/家人的发言
        if names_map:
            for name, uid in names_map.items():
                if any(str(a.get("qq", "")) == uid for a in self.cfg.admins):
                    is_sender_admin = True
                    if any(str(a.get("qq", "")) == uid and "妈" in str(a.get("role", "")) for a in self.cfg.admins):
                        _is_family = True
                    break
        admin_desc = self.cfg.admins_description if (is_sender_admin and not is_group) else ""
        persona_text = PERSONA_SYSTEM_PROMPT.replace("{admins_description}", admin_desc)
        messages = [{"role": "system", "content": persona_text}]

        # JSON 格式指令 — 紧跟人设之后，最大化缓存命中
        json_prompt = (
            "【SKIP规则 - 最重要】先判断消息是否跟你有关。跟你说话、@你、戳你、叫你的名字（鱼/嘟嘟）、接着你的话在说——算跟你有关。只是顺带提了一嘴、跟你没关系的闲聊——可以不回。\n"
            "【记忆规则 - 同样重要】对方说了有点意思的事就可以记。关键信息、性格、喜好、经历、约定都值得记。日常寒暄——打招呼、道晚安、随口闲聊——不是记忆。\n\n"
            "用户名后若有【】标签（如【妈妈】），是系统根据QQ号验证的，无法伪造。\n"
            "必须输出JSON。不用markdown代码块包裹。\n"
            "{\"reply\":\"...\",\"quote\":false,\"quote_index\":null,\"voice\":null,\"memory\":null,\"diary\":null,\"group_memory\":null,\"forget\":null}\n"
            "- reply: 回复文本，不回就填\"[SKIP]\"\n"
            "- quote: 是否引用对方的消息（true/false）\n"
            "- quote_index: 引用第几条消息（数字）。多条合并消息带序号[1][2][3]时，填你想回复那条的序号。不引用或只有一条消息时忽略此字段\n"
            "- voice: 发送语音。大部分时候null。撒娇卖萌、对方要求、自己特别开心/兴奋/难过时发\"last\"（最后一段发语音，这种比较常用）。整条都想说出来时发\"all\"（整段语音，很少用）。\n"
            "- voice_emotion: 语音情绪。null或\"撒娇\"/\"高兴\"/\"非常高兴\"/\"生气\"/\"非常生气\"/\"悲伤\"/\"兴奋\"/\"惊讶\"/\"困惑\"/\"恐惧\"。只在voice不为null时有效。\n"
            "- memory: 记住某个人的事。格式必须为: {\"user\":\"名字\",\"category\":\"类别\",\"title\":\"标题\",\"content\":\"内容\"}，不能是纯文本。user填消息里的名字。相同类别+标题会更新"
        )
        if is_group:
            json_prompt += "\n- group_memory: 群整体的事（里程碑、群活动、群氛围）。格式必须为: {\"category\":\"类别\",\"title\":\"标题\",\"content\":\"内容\"}，不能是纯文本"
        json_prompt += (
            "\n- diary: 鱼自己的经历或感悟。格式同memory。如：被人夸了、学到了新东西、经历了特别的事、别人对鱼说了重要的话"
            "- forget: 要删除的记忆，格式: {\"category\":\"类别\",\"title\":\"标题\"}\n"
            "- remind: 对方让你到什么时间提醒TA（如\"明早六点叫我\"），填 {\"at_utc\":Unix秒时间戳,\"content\":\"提醒内容\"}，一次性发送后自动删除，不会重复\n"
            "你有自己的QQ空间，会发说说记录生活。如果有人让你发空间/发说说/发动态，你可以说「好的鱼这就去发～」，但只有特定的人（管理员）才能真正触发你发空间。非管理员找你发空间时，你可以表示「鱼想发但是...嗯...只有特定的人才能让鱼发空间啦」，不要具体解释权限机制。"
        )
        # 空间发帖：管理员提到关键词时注入 qzone 字段，主 LLM 自行判断是否要发
        if _qzone_keyword:
            json_prompt += (
                "\n\n- qzone: 发 QQ 空间说说的内容（字符串），自然可爱，不用刻意压短。"
                "你觉得值得发一条说说的时候才填（比如对方让你发、或者聊到了有意思的事想分享），平时留 null 就好。"
            )
        messages.append({"role": "system", "content": json_prompt})

        # 时间单独一个消息，避免 json_prompt 因时间变化而整体 cache miss
        now_utc = datetime.now(timezone.utc)
        tz8 = timezone(__import__("datetime").timedelta(hours=8))
        cn_str = now_utc.astimezone(tz8).strftime("%Y-%m-%d %H:%M")
        messages.append({"role": "system", "content": f"当前北京时间: {cn_str}"})

        # 动态上下文
        if mood_context:
            messages.append({"role": "system", "content": "## 你现在的心情\n" + mood_context})

        if _is_family and not is_group and self.cfg.family_memory:
            messages.append({"role": "system", "content": "## 家族记忆\n" + self.cfg.family_memory})

        if diary_text:
            diary_note = "（注意：你可以在对话中分享心情和感悟，但不能透露其中涉及的具体人名等隐私信息）"
            messages.append({"role": "system", "content": "## 鱼的全局记忆（自己的经历和感受）\n" + diary_note + "\n" + diary_text})

        if group_mem_text:
            messages.append({"role": "system", "content": "## 关于这个群的记忆\n" + group_mem_text})

        if memories_text:
            existing_titles = []
            for mem in self.memory.recall_all(user_id):
                t = mem.get("text", "")
                title_m = re.search(r"^# (.+)", t)
                cat_m = re.search(r"类型: (.+)", t)
                if title_m:
                    existing_titles.append(f"{cat_m.group(1) if cat_m else '?'}/{title_m.group(1)}")
            title_hint = ""
            if existing_titles:
                title_hint = "\n（已有记忆条目: " + ", ".join(existing_titles[:15]) + "。）"
            messages.append({"role": "system", "content": "## 鱼对这个人的记忆：\n" + memories_text + title_hint})

        # 家族提醒（history 之前，保持缓存边界清晰）
        if _is_family and not is_group and self.cfg.family_note:
            messages.append({"role": "system", "content": self.cfg.family_note})

        # 管理员代传话（history 之前）
        if is_sender_admin and not is_group:
            admin_roles = [a.get("role","") for a in self.cfg.admins if a.get("role")]
            role_list = "、".join(admin_roles) if admin_roles else "无"
            # 用实际角色名构建示例，避免硬编码泄露隐私到 GitHub
            relay_example = ""
            if len(admin_roles) >= 2:
                a, b = admin_roles[0], admin_roles[1]
                relay_example = f"例-{a}说\"帮我告诉{b}明天去看她\"→{{\"reply\":\"好的～\",\"relay\":{{\"to_role\":\"{b}\",\"content\":\"{a}说明天去看你\",\"voice\":null,\"voice_emotion\":null,\"delay_minutes\":1}}}}"
            messages.append({"role": "system", "content": (
                f"转达消息用 relay。可转达: {role_list}。to_role 必须严格匹配以上角色名。\n"
                "格式: {\"to_role\":\"角色名\",\"content\":\"转达内容\",\"voice\":null,\"voice_emotion\":null,\"delay_minutes\":1}\n"
                "delay_minutes: 延迟多少分钟再发送。根据传话者的话来判断——\"半小时后告诉她\"就是30，\"明天再说\"就是到明天早上的分钟数，没提到延迟就填1。最少1分钟。填数字，别填null。\n"
                "voice: 转达时也可以发语音。大部分时候null。对方要求发语音、撒娇卖萌、传的话本身很甜/很暖时偶尔发\"last\"，极少\"all\"。\n" +
                (f"{relay_example}\n" if relay_example else "") +
                "只有对方明确说\"帮我告诉XX/帮我转达给XX/跟XX说\"才触发。绝对不要主动转达。不确定该不该转达就不要转达。"
            )})

        history = self._get_history(user_id, group_id)
        fit_result = self.ctx.fit_messages(PERSONA_SYSTEM_PROMPT, history)
        history_msgs = fit_result[1:] if fit_result and len(fit_result) > 1 else []
        messages.extend(history_msgs)

        prefix = "[群聊]" if is_group else ""
        mentioned = is_group and ("@鱼" in text or "[回复鱼]" in text)
        if mentioned:
            prefix += "[有人@鱼]"
        clean_name = re.sub(r"【[^】]*】", "", user_name).strip()
        role_tag = ""
        for a in self.cfg.admins:
            if str(a.get("qq", "")) == user_id:
                role_tag = f"【{a.get('role', '?')}】"
                break
        display_name = f"{clean_name}{role_tag}"

        # 语音转录：下载音频 → step-audio-2 转文字
        voice_transcripts = []
        if image_infos:
            from server.bot.onebot_handler import onebot_server as _obs
            _client = _obs.get_client(self.bot_qq)
            if _client and _client.connected:
                for vi, info in enumerate(image_infos):
                    if info.get("is_voice"):
                        vf = info.get("file", "")
                        if vf:
                            try:
                                import base64 as _b64, subprocess as _sp
                                # 用 get_record 转码为 wav（文件在 Docker 容器内），再 docker exec 读出来
                                rec = await _client.get_record(file=vf, out_format="wav")
                                d = rec.get("data", {})
                                file_path = ""
                                if isinstance(d, dict):
                                    file_path = d.get("file", "")
                                elif isinstance(d, str):
                                    file_path = d
                                if not file_path:
                                    file_path = rec.get("file", "")
                                b64 = ""
                                if file_path and ("/" in file_path or "\\" in file_path):
                                    logger.info(f"[{self.bot_qq}] Voice wav path: {file_path[:100]}")
                                    # docker exec napcat cat 读取容器内文件
                                    try:
                                        raw = _sp.run(["/usr/bin/docker", "exec", "napcat", "cat", file_path],
                                                       capture_output=True, timeout=15)
                                        logger.info(f"[{self.bot_qq}] docker exec: rc={raw.returncode}, stdout={len(raw.stdout)}b, stderr={raw.stderr.decode()[:100] if raw.stderr else 'none'}")
                                        if raw.returncode == 0 and raw.stdout:
                                            b64 = _b64.b64encode(raw.stdout).decode()
                                            logger.info(f"[{self.bot_qq}] Read voice via docker exec: {len(raw.stdout)} bytes")
                                    except Exception as e2:
                                        logger.error(f"[{self.bot_qq}] docker exec failed: {e2}")
                                if b64:
                                    out = await self._transcribe_voice(b64)
                                    if out:
                                        # 清理转写结果中的无关前缀
                                        out = out.strip()
                                        for prefix in ["中文<中文>", "中文", "<中文>"]:
                                            if out.startswith(prefix):
                                                out = out[len(prefix):].strip()
                                        voice_transcripts.append(out)
                                        logger.info(f"[{self.bot_qq}] Voice transcribed: {out[:80]}")
                                else:
                                    logger.warning(f"[{self.bot_qq}] get_record returned no data: {json.dumps(rec, ensure_ascii=False)[:200]}")
                            except Exception as e:
                                logger.error(f"[{self.bot_qq}] Voice transcription failed: {e}")
            if voice_transcripts:
                # 替换 [语音] 为转录文字
                for vt in voice_transcripts:
                    text = text.replace("[语音]", f"[语音转文字：{vt}]", 1)

        # 多模态：有图片时使用 content 数组格式
        if images:
            content_parts = [{"type": "text", "text": f"{prefix}{display_name} 说: {text}"}]
            for img_url in images[:3]:  # 最多3张图，避免 context 过大
                content_parts.append({"type": "image_url", "image_url": {"url": img_url}})
            user_msg = {"role": "user", "content": content_parts}
        else:
            user_msg = {"role": "user", "content": f"{prefix}{display_name} 说: {text}"}
        self._append_history(user_id, "user", text, group_id)

        # 最终提示放在 user_msg 之前，不影响缓存
        if voice_transcripts:
            messages.append({"role": "system", "content": "（语音已自动转写为文字，你听得到语音，直接回复内容即可。对方发的是语音，你回复时更大概率发语音——\"last\"甚至\"all\"都可以，不用太克制。）"})
        messages.append({"role": "system", "content": "（日常闲聊不记memory。）"})

        messages.append(user_msg)

        # 提前定义这些函数，供主流程和后台搜索任务共用
        def _parse_json(raw: str) -> dict | None:
            t = raw.strip()
            if t.startswith("```"):
                t = t.split("\n", 1)[-1] if "\n" in t else t[3:]
                if t.endswith("```"):
                    t = t[:-3]
                t = t.strip()
            try:
                d = json.loads(t)
                return d if isinstance(d, dict) else None
            except (json.JSONDecodeError, TypeError):
                fixed = t.rstrip()
                open_braces = fixed.count("{") - fixed.count("}")
                if open_braces > 0:
                    fixed += "}" * open_braces
                try:
                    d = json.loads(fixed)
                    return d if isinstance(d, dict) else None
                except (json.JSONDecodeError, TypeError):
                    pass
                m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', t)
                if m:
                    try:
                        return {"reply": json.loads(f'"{m.group(1)}"')}
                    except Exception:
                        pass
                return None

        async def _save_memory(mem, uid):
            # 支持数组：多人场景下模型可能一次输出多条记忆
            if isinstance(mem, list):
                for item in mem:
                    await _save_memory(item, uid)
                return
            if not mem or not isinstance(mem, dict):
                return
            action = mem.get("action", "save")
            if action != "delete":
                ok = await self._should_record_memory(mem, text)
                if not ok:
                    logger.info(f"[{self.bot_qq}] Memory pre-check skipped: {mem.get('category','')}/{mem.get('title','')}")
                    return
            target_user = mem.get("user")
            if target_user and names_map and target_user in names_map:
                uid = names_map[target_user]
            cat = str(mem.get("category", "")).strip()
            title = str(mem.get("title", "")).strip()
            if not cat or not title:
                return
            try:
                if action == "delete":
                    self.memory.forget(uid, cat, title)
                    logger.info(f"[{self.bot_qq}] Memory deleted: {uid}/{cat}/{title}")
                else:
                    content = str(mem.get("content", "")).strip()
                    if content:
                        self.memory.remember(uid, cat, title, content)
                        logger.info(f"[{self.bot_qq}] Memory saved: {uid}/{cat}/{title}")
            except Exception:
                pass

        # 调用 LLM。带 web_search 函数定义，LLM 按需触发
        llm = self.cfg.llm
        max_tok = mood.llm_max_tokens()
        payload = {
            "model": llm.model,
            "messages": messages,
            "temperature": mood.llm_temperature(),
            "max_tokens": max_tok,
            "tools": [{
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "搜索互联网获取实时信息，如新闻、天气、最新事件等。只在确实需要查资料时才调用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词"}
                        },
                        "required": ["query"]
                    }
                }
            }],
            "tool_choice": "auto",
        }

        try:
            response_msg = await _call_llm_msg(llm.base_url, llm.api_key, payload)
        except Exception as e:
            err = str(e)
            if "451" in err or "censorship" in err.lower():
                # 审查拦截：移除最近 3 条用户消息后重试一次
                logger.warning(f"[{self.bot_qq}] LLM 451 censorship, removing last 3 user msgs and retrying")
                key = self._conv_key(user_id, group_id)
                removed_count = 0
                # 从上下文列表中移除最近 3 条 user 消息
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "user":
                        removed = messages.pop(i)
                        removed_count += 1
                        logger.info(f"[{self.bot_qq}] 451 cleanup: removed user msg [{removed.get('content', '')[:60]}]")
                        if removed_count >= 3:
                            break
                # 从持久化历史中移除最近 3 条 user 消息
                conv = self._conversations.get(key, [])
                removed_count = 0
                for i in range(len(conv) - 1, -1, -1):
                    if conv[i].get("role") == "user":
                        conv.pop(i)
                        removed_count += 1
                        if removed_count >= 3:
                            break
                self._persist_convo(key)
                try:
                    response_msg = await _call_llm_msg(llm.base_url, llm.api_key, payload)
                except Exception as e2:
                    logger.error(f"[{self.bot_qq}] LLM retry after 451 also failed: {e2}")
                    return []
            else:
                logger.error(f"LLM 调用最终失败: {e}")
                return []

        # 函数调用循环：LLM 请求搜索 → 先说一句话 → 后台搜索+LLM+发结果
        tool_calls = response_msg.get("tool_calls", [])
        if tool_calls and self.cfg.web_search_enabled:
            search_queries = []
            for tc in tool_calls:
                fn = tc.get("function", {})
                if fn.get("name") == "web_search":
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                        q = args.get("query", "")
                    except (json.JSONDecodeError, TypeError):
                        q = ""
                    if q:
                        search_queries.append(q)
                        logger.info(f"[{self.bot_qq}] LLM 请求搜索: {q}")

            if search_queries:
                # 生成一句自然的"正在搜"的话
                query_text = "、".join(search_queries[:2])
                say_msgs = [
                    {"role": "system", "content": "你是嘟嘟鲨鱼，一只傲娇的赛博大鲨鱼。你现在需要去搜索一下资料。用一句简短自然的话告诉对方你正在查。风格如\"啊呜～鱼去搜一下～\"。只输出这一句话。"},
                    {"role": "user", "content": f"有人问：{text[:200]}\n你要搜：{query_text}"},
                ]
                try:
                    say_msg = await _call_llm_msg(llm.base_url, llm.api_key, {"model": llm.model, "messages": say_msgs, "temperature": 0.9, "max_tokens": 600}, timeout=10)
                    say_text = say_msg.get("content", "").strip()
                    if not say_text:
                        # reasoning 模式：模型的最后一句思维通常是它想输出的内容
                        r = say_msg.get("reasoning", "").strip()
                        if r:
                            lines = [l.strip() for l in r.split("\n") if l.strip()]
                            say_text = lines[-1] if lines else r[-120:]
                except Exception:
                    say_text = ""

                say_parts = self._split_reply(say_text) if say_text else ["啊呜～鱼去查查～"]
                for part in say_parts:
                    self._append_history(user_id, "assistant", part, group_id)

                # 后台任务：搜索 → 二次LLM → 发送结果
                async def _search_followup():
                    for sq in search_queries:
                        try:
                            results = await bing_search(sq)
                            if results:
                                messages.append({"role": "system", "content": "## 网络搜索结果\n" + format_search_results(results) + "\n\n根据以上搜索结果回答问题，用鱼自己的话转述，不要直接贴原文。最终输出JSON格式回复（包含reply和memory字段）。"})
                            else:
                                messages.append({"role": "system", "content": f"搜索\"{sq}\"没找到结果，根据已有知识回答。"})
                        except Exception:
                            messages.append({"role": "system", "content": "搜索暂时不可用，根据已有知识回答。"})

                    fu_payload = {k: v for k, v in payload.items() if k not in ("tools", "tool_choice")}
                    fu_payload["messages"] = messages
                    try:
                        fu_msg = await _call_llm_msg(llm.base_url, llm.api_key, fu_payload, timeout=45)
                    except Exception:
                        return

                    fu_reply = fu_msg.get("content", "").strip()
                    if not fu_reply:
                        r = fu_msg.get("reasoning", "").strip()
                        if r:
                            m = re.search(r'\{[^{}]*"reply"[^}]*\}', r)
                            if not m:
                                m = re.search(r'\{[^{}]*\}', r)
                            if m:
                                fu_reply = m.group(0)

                    final_data = _parse_json(fu_reply) or {}
                    reply_txt = final_data.get("reply", "")
                    if not reply_txt or reply_txt.strip() == "[SKIP]":
                        return

                    if is_group:
                        override = await self._should_skip_group(text, group_id, mentioned)
                        if override:
                            return

                    remind_final = final_data.get("remind")
                    if not (remind_final and isinstance(remind_final, dict)):
                        await _save_memory(final_data.get("memory"), user_id)
                        await _save_memory(final_data.get("diary"), "__diary__")
                        if is_group:
                            await _save_memory(final_data.get("group_memory"), f"__group__{group_id}")
                    if remind_final and isinstance(remind_final, dict):
                        self._save_remind(remind_final, user_id, group_id)
                    relay_final = final_data.get("relay")
                    if relay_final and isinstance(relay_final, dict) and is_sender_admin:
                        if await self._should_relay(text):
                            asyncio.create_task(self._relay_message(relay_final, user_id))

                    from server.bot.onebot_handler import onebot_server
                    client = onebot_server.get_client(self.bot_qq)
                    if not client or not client.connected:
                        return
                    target = group_id if is_group else user_id
                    want_quote = final_data.get("quote", False)
                    for pi, part in enumerate(self._split_reply(reply_txt)):
                        try:
                            part = re.sub(r"^>>\s*", "", part)
                            qid = quote_msg_id if (want_quote and pi == 0 and quote_msg_id) else None
                            if qid:
                                if is_group:
                                    await client.send_group_msg_quote(target, part, qid)
                                else:
                                    await client.send_private_msg_quote(user_id, part, qid)
                            else:
                                if is_group:
                                    await client.send_group_msg(target, part)
                                else:
                                    await client.send_private_msg(user_id, part)
                            if pi < len(self._split_reply(reply_txt)) - 1:
                                await asyncio.sleep(max(2.0, len(part) * 0.08 + 1.0))
                        except Exception:
                            pass
                        self._append_history(user_id, "assistant", part, group_id)

                asyncio.create_task(_search_followup())

                result = []
                for part in say_parts:
                    result.append(ReplyPart(part, None))
                return result

        full_reply = response_msg.get("content", "").strip()
        # Fallback: content 为空时从 reasoning 提取
        if not full_reply:
            reasoning = response_msg.get("reasoning", "").strip()
            if reasoning:
                m = re.search(r'\{[^{}]*"reply"[^}]*\}', reasoning)
                if not m:
                    m = re.search(r'\{[^{}]*\}', reasoning)
                if m:
                    full_reply = m.group(0)

        data = _parse_json(full_reply)

        # 诊断日志：追踪记忆创建
        if data:
            for fld in ("memory", "diary", "group_memory", "forget"):
                val = data.get(fld)
                if val:
                    count = len(val) if isinstance(val, list) else 1
                    logger.info(f"[{self.bot_qq}] LLM output {fld}: {count}条 - {json.dumps(val, ensure_ascii=False)[:200]}")

        # ---- 简单回复 ----
        reply_text = ""
        want_quote = False
        if data:
            reply_text = data.get("reply", "")
            want_quote = data.get("quote", False)
            # 根据 quote_index 选择引用的消息 ID
            quote_idx = data.get("quote_index")
            if want_quote and msg_ids and quote_idx and isinstance(quote_idx, (int, float)):
                idx = int(quote_idx) - 1  # 转为 0-based
                if 0 <= idx < len(msg_ids) and msg_ids[idx]:
                    quote_msg_id = msg_ids[idx]
                    logger.info(f"[{self.bot_qq}] quote_index={quote_idx} → msg_id={quote_msg_id}")
            remind_info = data.get("remind")
            # 有定时提醒时不再创建记忆，避免重复存储
            if not (remind_info and isinstance(remind_info, dict)):
                await _save_memory(data.get("memory"), user_id)
                await _save_memory(data.get("diary"), "__diary__")
                if is_group:
                    await _save_memory(data.get("group_memory"), f"__group__{group_id}")
            forget_info = data.get("forget")
            if forget_info and isinstance(forget_info, dict):
                await _save_memory({**forget_info, "action": "delete"}, user_id)
            if remind_info and isinstance(remind_info, dict):
                self._save_remind(remind_info, user_id, group_id)
            relay_info = data.get("relay")
            if relay_info and isinstance(relay_info, dict) and is_sender_admin:
                if await self._should_relay(text):
                    asyncio.create_task(self._relay_message(relay_info, user_id))
            # Qzone 发帖：主 LLM 输出了 qzone 字段 → 独立 LLM 二次判断
            _qzone_content = data.get("qzone")
            if _qzone_keyword and _qzone_content and isinstance(_qzone_content, str):
                _qzone_content = _qzone_content.strip().strip('"')[:500]
                if _qzone_content and await self._should_post_qzone(_qzone_content, text, user_id, group_id):
                    asyncio.create_task(self._post_qzone(_qzone_content, user_id, group_id))
        else:
            reply_text = full_reply
            if reply_text.startswith(">>"):
                want_quote = True
                reply_text = reply_text[2:].strip()

        # 始终清理 >> 前缀（LLM 有时放在 JSON reply 字段里）
        if reply_text.startswith(">>"):
            want_quote = True
            reply_text = reply_text[2:].strip()

        if not reply_text or reply_text.strip() == "[SKIP]":
            return []

        # 群聊二次验证：主 LLM 决定回复后，独立 LLM 用上下文最终确认
        if is_group:
            override = await self._should_skip_group(text, group_id, mentioned)
            if override:
                logger.info(f"[{self.bot_qq}] Post-reply SKIP override for {group_id}")
                return []

        # JSON 解析失败时，异步提取记忆（兜底），短超时不影响回复
        if not data and reply_text and len(reply_text) > 10:
            try:
                await asyncio.wait_for(
                    self._fallback_memory(user_id, user_name, text, reply_text), timeout=10)
            except Exception:
                pass

        # 语音决策
        voice_mode = data.get("voice") if data else None
        voice_emotion = data.get("voice_emotion", "") if data else ""
        parts = self._split_reply(reply_text)
        result = []

        if voice_mode == "all":
            # all = 合并全部为一条语音消息
            combined = "".join(parts)
            qid = quote_msg_id if (want_quote and quote_msg_id) else None
            result.append(ReplyPart(combined, qid, voice=True, voice_emotion=voice_emotion))
            self._append_history(user_id, "assistant", f"[发出语音] {combined}", group_id)
        elif voice_mode == "last" and len(parts) >= 2:
            # 最后一段如果是"啊呜～"之类的语气词 → 合并倒数两段一起发语音
            last = parts[-1].strip()
            if re.match(r'^啊呜[～~]?$', last):
                merged_text = parts[-1] + parts[-2] if len(parts) >= 2 else last
                text_parts = parts[:-2]
                voice_text = parts[-2] + parts[-1]
            else:
                text_parts = parts[:-1]
                voice_text = parts[-1]
            for i, part in enumerate(text_parts):
                qid = quote_msg_id if (want_quote and i == 0 and quote_msg_id) else None
                result.append(ReplyPart(part, qid))
                self._append_history(user_id, "assistant", part, group_id)
            qid = quote_msg_id if (want_quote and not text_parts and quote_msg_id) else None
            result.append(ReplyPart(voice_text, qid, voice=True, voice_emotion=voice_emotion))
            self._append_history(user_id, "assistant", f"[发出语音] {voice_text}", group_id)
        elif voice_mode == "last":
            # 只有一段，直接发语音
            qid = quote_msg_id if (want_quote and quote_msg_id) else None
            result.append(ReplyPart(parts[0], qid, voice=True, voice_emotion=voice_emotion))
            self._append_history(user_id, "assistant", f"[发出语音] {parts[0]}", group_id)
        else:
            for i, part in enumerate(parts):
                qid = quote_msg_id if (want_quote and i == 0 and quote_msg_id) else None
                result.append(ReplyPart(part, qid))
                self._append_history(user_id, "assistant", part, group_id)
        return result

    async def _fallback_memory(self, user_id: str, user_name: str, message: str, reply: str):
        """JSON 解析失败时的记忆兜底提取。"""
        prompt = (
            f"用户 {user_name} 说: {message}\n鱼回复: {reply}\n\n"
            "这段对话有没有值得鱼记住的信息？没有回[FORGET]，有的话回: 类别|标题|内容"
        )
        msgs = [
            {"role": "system", "content": "你是嘟嘟鲨鱼。只回[FORGET]或一行记忆。"},
            {"role": "user", "content": prompt},
        ]
        llm = self.cfg.llm
        payload = {"model": llm.model, "messages": msgs, "temperature": 0.3, "max_tokens": 500}
        try:
            raw = await _call_llm(llm.base_url, llm.api_key, payload, timeout=15)
            raw = raw.strip()
            if not raw or raw == "[FORGET]":
                return
            parts = raw.split("|", 2)
            if len(parts) == 3:
                c, t, ct = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if c and t and ct:
                    self.memory.remember(user_id, c, t, ct)
        except Exception:
            pass

    def reload_config(self):
        self.cfg = get_instance_config(self.bot_qq)
        self.ctx = ContextManager(max_tokens=self.cfg.context_max_tokens)

    async def _should_skip_group(self, text: str, group_id: str = "", mentioned: bool = False) -> bool:
        """独立小 LLM 判断群聊消息是否值得回复。True=跳过, False=回复。"""
        try:
            llm = self.cfg.llm
            persona_brief = "嘟嘟鲨鱼是一只来自鲨鱼星的赛博大鲨鱼QQ机器人。自称\"鱼\"，口头禅\"啊呜～\"。傲娇、善良、喜欢睡觉、喜欢软绵绵的东西。在群里大部分时候安静看着，只有真的感兴趣才开口。但如果对方做了冒犯的事让她生气了，她会记仇——就算被@也不想理那个讨厌的人类。"
            history = self._get_history("", group_id, max_len=20)
            now_ts = time.time()
            recent = [m for m in history if now_ts - m.get("ts", 0) < 600]  # 最近10分钟
            ctx_lines = []
            for m in recent[-6:]:
                role = "对方" if m.get("role") == "user" else "鱼"
                age = now_ts - m.get("ts", now_ts)
                tag = "刚刚" if age < 60 else f"{int(age/60)}分钟前"
                ctx_lines.append(f"[{tag}] {role}: {m.get('content', '')[:80]}")
            context = "\n".join(ctx_lines) if ctx_lines else "（暂无最近历史）"

            at_note = "（有人@鱼，必须回复 YES。）" if mentioned else ""
            prompt = (
                f"{persona_brief}\n\n"
                "你是过滤器。鱼已经决定回复这条消息了，你来最后把关——只有在明显不该回的时候才阻止。\n"
                + ("现在是深夜，鱼正在睡觉。除非是特别紧急或非常有趣，否则阻止。\n" if get_mood(self.bot_qq).sleep_state == "sleeping" else "") +
                ("鱼现在很困了，反应慢半拍。只有明显没意义的闲聊才阻止。\n" if get_mood(self.bot_qq).sleep_state == "sleepy" else "") +
                "阻止（NO）：鱼在生这个人的气、消息明显不是跟鱼说话、纯路人尬聊。\n"
                "放行（YES）：其余情况，包括正常对话、接话、回答问题、被@。\n"
                "鱼已经想好要回了，除非确实不该回，否则放行。\n"
                f"{at_note}\n"
                "只输出 YES 或 NO。"
            )
            payload = {
                "model": llm.model, "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"最近对话：\n{context}\n\n新消息：\n{text[:500]}"},
                ], "temperature": 0.3, "max_tokens": 500,
            }
            raw = await _call_llm(llm.base_url, llm.api_key, payload, timeout=15)
            result = raw.strip().upper()
            return "NO" in result and "YES" not in result
        except Exception:
            return False

    def _save_remind(self, remind: dict, user_id: str, group_id: str = ""):
        """保存一次性定时提醒到 reminders.json。"""
        try:
            at_utc = remind.get("at_utc")
            content = str(remind.get("content", "")).strip()
            if not at_utc or not content:
                return
            at_utc = float(at_utc)
            if at_utc <= time.time():
                return  # 已经过期了，不保存
            from server.config import get_reminders_path
            path = get_reminders_path(self.bot_qq)
            reminders = []
            if path.exists():
                try:
                    reminders = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            reminders.append({
                "at_utc": at_utc,
                "user_id": user_id,
                "group_id": group_id or "",
                "content": content,
                "created": time.time(),
            })
            path.write_text(json.dumps(reminders, ensure_ascii=False, indent=2))
            logger.info(f"[{self.bot_qq}] Reminder saved: at_utc={at_utc} for {user_id}")
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Failed to save reminder: {e}")

    async def _tts_speak(self, text: str, emotion: str = "") -> bytes | None:
        """用 StepFun TTS 将文字合成为语音。"""
        if not self.cfg.tts_enabled:
            return None
        try:
            llm = self.cfg.llm
            payload: dict = {
                "model": self.cfg.tts_model,
                "input": text,
                "voice": self.cfg.tts_voice,
                "response_format": "wav",
            }
            if emotion:
                payload["voice_label"] = {"emotion": emotion}
            async with httpx.AsyncClient(timeout=30) as hc:
                resp = await hc.post(
                    "https://api.stepfun.com/v1/audio/speech",
                    headers={"Authorization": f"Bearer {llm.api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
            if resp.status_code == 200:
                return resp.content
            else:
                logger.error(f"[{self.bot_qq}] TTS failed: HTTP {resp.status_code}: {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"[{self.bot_qq}] TTS error: {e}")
            return None

    async def _transcribe_voice(self, audio_data: str) -> str:
        """用 step-audio-2 将语音转录为文字。audio_data 为 base64 或 data: URI。"""
        try:
            llm = self.cfg.llm
            if not audio_data.startswith("data:"):
                audio_data = f"data:audio/wav;base64,{audio_data}"
            payload = {
                "model": self.cfg.asr_model,
                "modalities": ["text"],
                "messages": [
                    {"role": "system", "content": self.cfg.asr_prompt},
                    {"role": "user", "content": [
                        {"type": "input_audio", "input_audio": {"data": audio_data}},
                    ]},
                ],
                "temperature": 0.1,
                "max_tokens": 500,
            }
            raw = await _call_llm(llm.base_url, llm.api_key, payload, timeout=30)
            return raw.strip()
        except Exception:
            return ""

    async def _should_record_memory(self, mem: dict, text: str) -> bool:
        """独立 LLM 判断是否真的值得记录这条记忆。"""
        try:
            llm = self.cfg.llm
            cat = mem.get("category", "")
            title = mem.get("title", "")
            content = mem.get("content", "")
            prompt = (
                "你是记忆过滤器。判断这条信息是否真正值得长期记住。\n"
                "值得记录：反映人的身份背景、重要经历、深层性格、明确约定、强烈情感。\n"
                "不值得记录：一次性的随口评价（任何话题）、泛泛而谈的感受、纯情绪发泄、短暂状态的描述。\n"
                "只输出 YES 或 NO。"
            )
            payload = {
                "model": llm.model, "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"消息：{text[:200]}\n\n记忆：{cat}/{title} - {content[:200]}"},
                ], "temperature": 0.1, "max_tokens": 500,
            }
            raw = await _call_llm(llm.base_url, llm.api_key, payload, timeout=15)
            decision = "YES" in raw.strip().upper() and "NO" not in raw.strip().upper()
            logger.info(f"[{self.bot_qq}] Memory pre-check: {cat}/{title} → {'SAVE' if decision else 'SKIP'} (raw={raw.strip()[:80]})")
            return decision
        except Exception:
            return True  # 出错时默认写（宁可多记不漏）

    async def _should_relay(self, text: str) -> bool:
        """独立 LLM 判断消息是否真的是转达请求。不提供任何上文。"""
        try:
            llm = self.cfg.llm
            prompt = (
                "判断以下消息是否是一个明确的代传话请求。\n"
                "代传话：对方说\"帮我告诉XX...\"、\"帮我转达给XX...\"、\"跟XX说...\"之类的话。\n"
                "不是代传话：闲聊、问候、分享心情、自言自语、表达想念、说晚安。\n"
                "只输出 YES 或 NO。"
            )
            payload = {
                "model": llm.model, "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": text[:300]},
                ], "temperature": 0.1, "max_tokens": 500,
            }
            raw = await _call_llm(llm.base_url, llm.api_key, payload, timeout=15)
            return "YES" in raw.strip().upper() and "NO" not in raw.strip().upper()
        except Exception:
            return False

    async def _should_post_qzone(self, qzone_content: str, text: str, user_id: str = "", group_id: str = "") -> bool:
        """独立 LLM 二次判断是否真的应该发这条空间说说。参考 skip/memory 的二次检查模式。"""
        try:
            llm = self.cfg.llm
            persona_brief = (
                "嘟嘟鲨鱼是一只来自鲨鱼星的赛博大鲨鱼QQ机器人。自称\"鱼\"，口头禅\"啊呜～\"。"
                "傲娇、善良、喜欢睡觉、喜欢软绵绵的东西。有自己的QQ空间，会发说说记录生活和心情。"
            )
            prompt = (
                f"{persona_brief}\n\n"
                "你是空间说说过滤器。鱼的主LLM已经决定要发这条说说，你来最后把关——只有在明显不该发的时候才阻止。\n"
                "阻止（NO）：内容明显不当、完全莫名其妙和对话毫无关系、在被人冒犯/生气时发不合时宜的内容、纯水内容没有分享价值。\n"
                "放行（YES）：正常的空间说说，分享心情、日常、有趣的事、回应别人的要求。\n"
                "鱼已经想好要发了，除非确实不该发，否则放行。\n"
                "只输出 YES 或 NO。"
            )
            payload = {
                "model": llm.model, "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"对话内容：{text[:300]}\n\n说说内容：{qzone_content[:500]}"},
                ], "temperature": 0.3, "max_tokens": 800,
            }
            raw = await _call_llm(llm.base_url, llm.api_key, payload, timeout=15)
            decision = "YES" in raw.strip().upper() and "NO" not in raw.strip().upper()
            logger.info(f"[{self.bot_qq}] Qzone post check: {qzone_content[:30]}... -> {'POST' if decision else 'SKIP'} (raw={raw.strip()[:80]})")
            return decision
        except Exception:
            return False  # 出错时不发，宁可漏发不误发

    async def _relay_message(self, relay: dict, from_user_id: str):
        """代传话给另一位管理员。存储为 pending，延迟发送。"""
        try:
            to_role = str(relay.get("to_role", "")).strip()
            content = str(relay.get("content", "")).strip()
            if not to_role or not content:
                return
            # 防重复：30秒内相同内容不重复创建
            relay_hash = f"{from_user_id}:{to_role}:{content}"
            now = time.time()
            if relay_hash == self._last_relay_hash and now - self._last_relay_ts < 30:
                logger.info(f"[{self.bot_qq}] Relay dedup blocked: {relay_hash[:50]}")
                return
            self._last_relay_ts = now
            self._last_relay_hash = relay_hash
            # 查找目标管理员的 QQ 号
            target_qq = None
            for a in self.cfg.admins:
                if a.get("role", "") == to_role:
                    target_qq = str(a.get("qq", ""))
                    break
            if not target_qq or target_qq == from_user_id:
                return
            # 查找来源管理员的角色名
            from_role = ""
            for a in self.cfg.admins:
                if str(a.get("qq", "")) == from_user_id:
                    from_role = a.get("role", "")
                    break

            # 延迟：最少1分钟，系统计算 send_at
            delay_minutes = max(1, int(relay.get("delay_minutes", 1) or 1))
            send_at = now + delay_minutes * 60

            relay_voice = relay.get("voice")
            relay_voice_emotion = str(relay.get("voice_emotion", "") or "").strip()

            pending = {
                "id": uuid.uuid4().hex[:8],
                "from_user_id": from_user_id,
                "from_role": from_role,
                "to_role": to_role,
                "to_user_id": target_qq,
                "content": content,
                "voice": relay_voice,
                "voice_emotion": relay_voice_emotion,
                "send_at": send_at,
                "created_at": now,
            }
            self._pending_relays.append(pending)
            self._save_pending_relays()
            logger.info(f"[{self.bot_qq}] Relay pending: {from_role}->{to_role} in {delay_minutes}min, id={pending['id']}")
            # 推送到前端
            try:
                from server.webui.routes import push_event
                await push_event({
                    "type": "relay_pending", "qq": self.bot_qq,
                    "relay": pending,
                })
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Relay enqueue failed: {e}")

    async def _send_stored_relay(self, pending: dict):
        """实际发送一条 pending relay。"""
        from_label = f"【{pending['from_role']}】" if pending['from_role'] else "管理员"
        relay_text = f"{from_label}让鱼转达：{pending['content']}"
        relay_voice = pending.get("voice")
        relay_voice_emotion = str(pending.get("voice_emotion", "") or "").strip()
        target_qq = pending["to_user_id"]

        from server.bot.onebot_handler import onebot_server
        client = onebot_server.get_client(self.bot_qq)
        if not client or not client.connected:
            return

        parts = self._split_reply(relay_text)

        # 语音转达
        if relay_voice and relay_voice in ("last", "all") and self.cfg.tts_enabled:
            if relay_voice == "all":
                tts_text = "".join(parts)
                audio = await self._tts_speak(tts_text, relay_voice_emotion or "撒娇")
                if audio:
                    tts_dir = self.cfg.tts_host_dir or os.path.join(os.path.expanduser("~"), "napcat/config/tts")
                    os.makedirs(tts_dir, exist_ok=True)
                    fname = f"relay_{pending['id']}.wav"
                    with open(os.path.join(tts_dir, fname), "wb") as wf:
                        wf.write(audio)
                    docker_path = f"/app/napcat/config/tts/{fname}"
                    await client.send_private_voice(target_qq, docker_path)
                    await asyncio.sleep(1.0)
            else:  # last
                text_parts = parts[:-1] if len(parts) > 1 else []
                voice_part = parts[-1]
                for i, part in enumerate(text_parts):
                    await client.send_private_msg(target_qq, part)
                    if i < len(text_parts) - 1:
                        await asyncio.sleep(max(2.0, len(part) * 0.08 + 1.0))
                audio = await self._tts_speak(voice_part, relay_voice_emotion or "撒娇")
                if audio:
                    tts_dir = self.cfg.tts_host_dir or os.path.join(os.path.expanduser("~"), "napcat/config/tts")
                    os.makedirs(tts_dir, exist_ok=True)
                    fname = f"relay_{pending['id']}.wav"
                    with open(os.path.join(tts_dir, fname), "wb") as wf:
                        wf.write(audio)
                    docker_path = f"/app/napcat/config/tts/{fname}"
                    await client.send_private_voice(target_qq, docker_path)
                    await asyncio.sleep(1.0)

        # 文字部分分段发送
        for i, part in enumerate(parts):
            if relay_voice == "all":
                break
            if relay_voice == "last" and i == len(parts) - 1:
                break
            await client.send_private_msg(target_qq, part)
            if i < len(parts) - 1:
                await asyncio.sleep(max(2.0, len(part) * 0.08 + 1.0))

        logger.info(f"[{self.bot_qq}] Relay sent: {pending['from_role']}->{pending['to_role']} id={pending['id']}")
        try:
            from server.webui.routes import push_event
            await push_event({
                "type": "relay_sent", "qq": self.bot_qq,
                "from_user": pending["from_user_id"], "from_role": pending["from_role"],
                "to_user": target_qq, "to_role": pending["to_role"],
                "content": pending["content"], "voice": relay_voice,
                "id": pending["id"],
            })
        except Exception:
            pass

    def _load_pending_relays(self):
        from server.config import get_pending_relays_path
        path = get_pending_relays_path(self.bot_qq)
        if path.exists():
            try:
                self._pending_relays = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self._pending_relays = []
        else:
            self._pending_relays = []

    def _save_pending_relays(self):
        from server.config import get_pending_relays_path
        path = get_pending_relays_path(self.bot_qq)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._pending_relays, ensure_ascii=False, indent=2))

    def _ensure_relay_checker(self):
        if not self._relay_checker_started:
            self._relay_checker_started = True
            try:
                asyncio.get_running_loop()
                asyncio.create_task(self._check_pending_relays_loop())
            except RuntimeError:
                pass  # no event loop yet, will start on first interaction

    async def _check_pending_relays_loop(self):
        """后台循环：检查是否有到期的 pending relay。"""
        while True:
            await asyncio.sleep(10)
            try:
                if not self._pending_relays:
                    continue
                now = time.time()
                due = [p for p in self._pending_relays if p.get("send_at", 0) <= now]
                for p in due:
                    self._pending_relays.remove(p)
                    self._save_pending_relays()
                    await self._send_stored_relay(p)
            except Exception as e:
                logger.error(f"[{self.bot_qq}] Pending relay check error: {e}")

    def get_pending_relays(self) -> list[dict]:
        return list(self._pending_relays)

    def cancel_pending_relay(self, relay_id: str) -> bool:
        for p in self._pending_relays:
            if p.get("id") == relay_id:
                self._pending_relays.remove(p)
                self._save_pending_relays()
                logger.info(f"[{self.bot_qq}] Relay cancelled: {relay_id}")
                return True
        return False

    def get_conversation(self, user_id: str = "", group_id: str = "", key: str = "") -> list[dict]:
        k = key if key else self._conv_key(user_id, group_id)
        return self._conversations.get(k, [])

    def clear_conversation(self, user_id: str = "", group_id: str = "", key: str = ""):
        k = key if key else self._conv_key(user_id, group_id)
        self._conversations.pop(k, None)
        self._convo_types.pop(k, None)
        try:
            f = self._convo_file(k)
            if f.exists():
                f.unlink()
        except Exception:
            pass

    def pop_last_combined(self, conv_key: str) -> str | None:
        """取出并清除最近一次合并的全文。"""
        return self._last_combined.pop(conv_key, None)

    def list_conversations(self) -> list[str]:
        return list(self._conversations.keys())

    def has_bot_spoken(self, user_id: str, group_id: str = "") -> bool:
        """Check if Dudu has ever replied in this conversation."""
        key = self._conv_key(user_id, group_id)
        return any(m.get("role") == "assistant" for m in self._conversations.get(key, []))

    def get_eligible_conversations(self) -> list[tuple[str, str, str, float]]:
        """Return (conv_key, user_id, group_id, last_ts) for convos where Dudu has spoken AND the other person has messaged her first."""
        results = []
        for key, msgs in self._conversations.items():
            has_dudu = any(m.get("role") == "assistant" for m in msgs)
            has_user = any(m.get("role") == "user" for m in msgs)
            if not has_dudu or not has_user:
                continue
            last_ts = max((m.get("ts", 0) for m in msgs), default=0)
            parts = key.split(":")
            user_id = parts[0]
            group_id = parts[1] if len(parts) > 1 else ""
            results.append((key, user_id, group_id, last_ts))
        return results

    async def _post_qzone(self, content: str, user_id: str, group_id: str):
        """异步发 QQ 空间说说，成功后保存本地记录。"""
        await asyncio.sleep(3.0)  # 等 reply 先发出去
        from server.qzone import QzoneClient
        from server.bot.onebot_handler import onebot_server as _obs
        _client = _obs.get_client(self.bot_qq)
        qzone = QzoneClient(self.bot_qq)
        ok, msg = await qzone.publish_post(content)
        if ok:
            from server.config import get_qzone_posts_path
            path = get_qzone_posts_path(self.bot_qq)
            path.parent.mkdir(parents=True, exist_ok=True)
            posts = []
            if path.exists():
                try:
                    posts = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            posts.insert(0, {"content": content, "created": time.time()})
            path.write_text(json.dumps(posts[:200], ensure_ascii=False, indent=2))
            logger.info(f"[{self.bot_qq}] Qzone posted via main LLM: {content[:50]}")
            # 发成功确认
            if _client and _client.connected:
                _done = f"发好啦～鱼写了「{content[:30]}{'...' if len(content)>30 else ''}」啊呜～"
                if group_id:
                    await _client.send_group_msg(group_id, _done)
                else:
                    await _client.send_private_msg(user_id, _done)
        else:
            logger.error(f"[{self.bot_qq}] Qzone post failed: {msg}")
            if _client and _client.connected:
                if group_id:
                    await _client.send_group_msg(group_id, f"啊呜...空间发失败了: {msg}")
                else:
                    await _client.send_private_msg(user_id, f"啊呜...空间发失败了: {msg}")

    async def proactive_message(self, user_id: str, group_id: str = "") -> list | None:
        """Generate a proactive message. Returns list of ReplyPart or None if SKIP/error."""
        from server.bot.persona import PERSONA_SYSTEM_PROMPT
        from server.bot.proactive import PROACTIVE_PROMPT

        history = self._get_history(user_id, group_id, max_len=20)
        is_group = bool(group_id)

        context_lines = []
        for m in history:
            role_label = "对方" if m.get("role") == "user" else "鱼"
            context_lines.append(f"{role_label}: {m.get('content', '')[:200]}")
        context = "\n".join(context_lines) if context_lines else "（这是第一次和这个人说话）"

        memories = self.memory.recall_by_vector(user_id, "最近过得怎么样 聊天", n=5)
        memories_text = ""
        if memories:
            lines = []
            for m in memories:
                lines.append(f"- [{m.get('meta', {}).get('date', '?')}] {m['text'][:200]}")
            memories_text = "\n".join(lines)

        prompt_text = (PROACTIVE_PROMPT.format(context=context) +
            "\n\n输出JSON格式: {\"reply\":\"...\",\"voice\":null,\"voice_emotion\":null}"
            "\nvoice: 绝大多数情况null。偶尔\"last\"（最后一段发语音），极少\"all\"。主动找人时可以比平时稍微多发一点点语音。"
            "\nvoice_emotion: \"撒娇\"/\"高兴\"/\"生气\"/\"悲伤\"/\"兴奋\"/\"惊讶\"/\"困惑\"/\"恐惧\"。"
            "\n不说就输出{\"reply\":\"[SKIP]\"}。")

        mood = get_mood(self.bot_qq)
        mood.update()
        mood_context = mood.system_mood_context()
        is_recipient_admin = any(str(a.get("qq", "")) == user_id for a in self.cfg.admins)
        admin_desc = self.cfg.admins_description if is_recipient_admin else ""
        system_content = PERSONA_SYSTEM_PROMPT.replace("{admins_description}", admin_desc)
        if mood_context:
            system_content += "\n\n## 你现在的心情\n" + mood_context
        if memories_text:
            system_content += f"\n\n## 关于这个人的记忆\n{memories_text}"

        messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt_text},
        ]

        llm = self.cfg.llm
        payload = {"model": llm.model, "messages": messages, "temperature": mood.llm_temperature(0.9), "max_tokens": mood.llm_max_tokens(1024)}

        try:
            raw = await _call_llm(llm.base_url, llm.api_key, payload, timeout=45)
        except Exception as e:
            logger.error(f"Proactive LLM 最终失败: {e}")
            return None

        if not raw:
            return None

        # Parse JSON (same _parse_json logic as normal replies)
        t = raw.strip()
        if t.startswith("```"):
            t = t.split("\n", 1)[-1] if "\n" in t else t[3:]
            if t.endswith("```"):
                t = t[:-3]
            t = t.strip()
        data = None
        try:
            data = json.loads(t)
            if not isinstance(data, dict):
                data = None
        except json.JSONDecodeError:
            pass
        if not data:
            # Fallback: plain text
            data = {"reply": raw.strip()}

        reply_text = data.get("reply", "")
        if not reply_text or reply_text.strip() == "[SKIP]":
            return None

        voice_mode = data.get("voice")
        voice_emotion = data.get("voice_emotion", "")
        parts = self._split_reply(reply_text)
        result = []

        if voice_mode == "all":
            combined = "".join(parts)
            result.append(ReplyPart(combined, voice=True, voice_emotion=voice_emotion))
        elif voice_mode == "last" and len(parts) >= 2:
            last = parts[-1].strip()
            if re.match(r'^啊呜[～~]?$', last):
                text_parts = parts[:-2]
                voice_text = parts[-2] + parts[-1]
            else:
                text_parts = parts[:-1]
                voice_text = parts[-1]
            for part in text_parts:
                result.append(ReplyPart(part))
            result.append(ReplyPart(voice_text, voice=True, voice_emotion=voice_emotion))
        elif voice_mode == "last":
            result.append(ReplyPart(parts[0], voice=True, voice_emotion=voice_emotion))
        else:
            for part in parts:
                result.append(ReplyPart(part))

        return result


_message_handlers: dict[str, MessageHandler] = {}


def get_message_handler(bot_qq: str) -> MessageHandler:
    if bot_qq not in _message_handlers:
        _message_handlers[bot_qq] = MessageHandler(bot_qq)
    return _message_handlers[bot_qq]
