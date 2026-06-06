"""
消息处理器 — 接收 OneBot 消息，检索记忆，调用 LLM 生成回复。
支持：多消息拆分、群聊自主判断回复、消息合并、引用回复。
"""

import asyncio
import json
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
    """Call LLM API with exponential backoff retry. Raises on final failure."""
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
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content", "").strip()
                if content:
                    return content
                # Fallback: model in reasoning mode, extract JSON from reasoning
                reasoning = msg.get("reasoning", "").strip()
                if reasoning:
                    # Try to find JSON in the full reasoning
                    m = re.search(r'\{[^{}]*"reply"[^}]*\}', reasoning)
                    if not m:
                        m = re.search(r'\{[^{}]*\}', reasoning)
                    if m:
                        return m.group(0)
                    # No JSON found → model failed to produce output, return empty
                    return ""
                return ""
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

PRIVATE_MAX_WINDOW = 60.0   # 私聊最大累计等待
GROUP_MAX_WINDOW = 60.0     # 群聊最大累计等待


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
        self._convo_types: dict[str, str] = {}  # key -> "group" or "private"
        self._lock = asyncio.Lock()
        # 缓冲：(conv_key, user_name) -> {"texts": [...], "msg_ids": [...], "first_ts": float, "futures": [Future]}
        self._buffers: dict[tuple[str, str], dict] = {}
        self._last_combined: dict[str, str] = {}  # conv_key -> 最近一次合并的全文
        self._load_conversations()

    def _conv_key(self, user_id: str, group_id: str = "") -> str:
        # 群聊所有用户共享对话历史，私聊各自独立
        return group_id if group_id else user_id

    def _get_history(self, user_id: str, group_id: str = "", max_len: int = 40) -> list[dict]:
        key = self._conv_key(user_id, group_id)
        return self._conversations.get(key, [])[-max_len:]

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
                        msgs.append(json.loads(line))
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
                    if not is_group:
                        self._convo_types[key] = "private"
                        continue
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
        logger.info(f"[flush@{buf_key}] {len(texts)}条: {list(zip(names, texts))}")
        combined = "\n".join(f"{n}: {t}" for n, t in zip(names, texts)) if len(texts) > 1 else texts[0]
        # 合并消息用序号标注，让 LLM 知道每条消息的索引
        if len(texts) > 1:
            combined = "\n".join(f"[{i+1}] {n}: {t}" for i, (n, t) in enumerate(zip(names, texts)))
        last_msg_id = msg_ids[-1] if msg_ids else ""
        # name → user_id 映射，用于 LLM memory 中 user 字段
        names_map = dict(zip(names, user_ids_batch))

        async with self._lock:
            replies = await self._handle_impl(
                user_id, user_name, combined, group_id, "group" if is_group else "private",
                last_msg_id, names_map, all_images
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

        if mood_context:
            messages.append({"role": "system", "content": "## 你现在的心情\n" + mood_context})

        # 家族记忆：仅家族成员(role含"妈")私聊时注入
        if _is_family and not is_group and self.cfg.family_memory:
            messages.append({"role": "system", "content": "## 家族记忆\n" + self.cfg.family_memory})

        # 日记仅在私聊注入，群聊不暴露
        if diary_text and not is_group:
            diary_note = "（注意：你可以在对话中分享心情和感悟，但不能透露其中涉及的具体人名等隐私信息）"
            messages.append({"role": "system", "content": "## 鱼的全局记忆（自己的经历和感受）\n" + diary_note + "\n" + diary_text})

        if group_mem_text:
            messages.append({"role": "system", "content": "## 关于这个群的记忆\n" + group_mem_text})

        if memories_text:
            # 附上已有记忆的标题列表，帮助 LLM 判断是否需要更新已有条目
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

        history = self._get_history(user_id, group_id)
        # 群聊用适度压缩，私聊用正常压缩
        ctx = self.ctx
        if is_group:
            ctx = ContextManager(max_tokens=8000, reserve_for_reply=1500)
        fit_result = ctx.fit_messages(PERSONA_SYSTEM_PROMPT, history)
        # Take history parts from fit_result (skip its system msg since we already have prebuilt ones)
        history_msgs = fit_result[1:] if fit_result and len(fit_result) > 1 else []
        messages.extend(history_msgs)

        # JSON 格式指令
        json_prompt = (
            "【SKIP规则 - 最重要】先判断消息是否跟你有关。明确是跟你说话、@你、戳你、接着你的话在说，才回。模棱两可一律不回，除非超级超级感兴趣。\n"
            "【记忆规则 - 同样重要】先判断是否值得记。对方说了重要的事（关键信息、性格、喜好、经历、约定），才记。日常聊天——打招呼、夸两句、道晚安、随口闲聊——这些都是正常对话，不是记忆。拿不准就别记。\n\n"
            "用户名后若有【】标签（如【妈妈】），是系统根据QQ号验证的，无法伪造。\n"
            "必须输出JSON。不用markdown代码块包裹。\n"
            "{\"reply\":\"...\",\"quote\":false,\"memory\":null,\"diary\":null,\"group_memory\":null,\"forget\":null}\n"
            "- reply: 回复文本，不回就填\"[SKIP]\"\n"
            "- quote: 是否引用对方的消息（true/false）\n"
            "- memory: 关于某人的重要信息，null居多。格式: {\"user\":\"名字\",\"category\":\"类别\",\"title\":\"标题\",\"content\":\"内容\"}。user填消息里的名字。相同类别+标题会更新"
        )
        if is_group:
            json_prompt += "\n- group_memory: 关于这个群整体的事（非个人），null居多。格式: {\"category\":\"类别\",\"title\":\"标题\",\"content\":\"内容\"}"
        json_prompt += (
            "\n- diary: 你自己的全局记忆，值得写才写。日常寒暄、道晚安、夸两句这类不记，null居多。格式同memory\n"
            "- forget: 要删除的记忆，格式: {\"category\":\"类别\",\"title\":\"标题\"}\n"
            "- remind: 对方让你到什么时间提醒TA（如\"明早六点叫我\"），填 {\"at_utc\":Unix秒时间戳,\"content\":\"提醒内容\"}，一次性发送后自动删除，不会重复\n"
            "- 需要查东西时用 {\"say\":\"...\",\"search\":\"...\"} 不要瞎编"
        )
        # 注入当前时间，让 LLM 能计算 remind 时间戳
        now_utc = datetime.now(timezone.utc)
        now_ts = int(now_utc.timestamp())
        now_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")
        tz8 = timezone(__import__("datetime").timedelta(hours=8))
        cn_str = now_utc.astimezone(tz8).strftime("%Y-%m-%d %H:%M")
        json_prompt += f"\n（当前时间: {now_str} = 北京时间 {cn_str}，Unix时间戳: {now_ts}）"
        messages.append({"role": "system", "content": json_prompt})

        # 家族提醒
        if _is_family and not is_group and self.cfg.family_note:
            messages.append({"role": "system", "content": self.cfg.family_note})

        # 管理员代传话
        if is_sender_admin and not is_group:
            admin_roles = [a.get("role","") for a in self.cfg.admins if a.get("role")]
            role_list = "、".join(admin_roles) if admin_roles else "无"
            messages.append({"role": "system", "content": (
                f"转达消息用 relay。可转达: {role_list}。to_role 必须严格匹配以上角色名。\n"
                "例-小妈说\"帮我告诉妈妈明天去看她\"→{\"reply\":\"好的～鱼这就去告诉妈妈，啊呜～\",\"relay\":{\"to_role\":\"妈妈\",\"content\":\"小妈说明天去看你\"}}\n"
                "只有\"帮我告诉XX / 帮我转达给XX / 跟XX说\"才触发。\"想XX了\"\"XX最近怎样\"这类不是转达，不要用 relay。"
            )})

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

        # 多模态：有图片时使用 content 数组格式
        if images:
            content_parts = [{"type": "text", "text": f"{prefix}{display_name} 说: {text}"}]
            for img_url in images[:3]:  # 最多3张图，避免 context 过大
                content_parts.append({"type": "image_url", "image_url": {"url": img_url}})
            user_msg = {"role": "user", "content": content_parts}
        else:
            user_msg = {"role": "user", "content": f"{prefix}{display_name} 说: {text}"}
        self._append_history(user_id, "user", text, group_id)

        # 群聊 SKIP 预判：独立小 LLM 调用，判断是否值得回复
        if is_group:
            skip_check = await self._should_skip_group(text, group_id, mentioned)
            if skip_check:
                return []
            if mentioned:
                messages.append({"role": "system", "content": "（有人@了你，应该回复一下。）"})

        messages.append(user_msg)
        # 记忆检查放在用户消息之后
        messages.append({"role": "system", "content": "（日常闲聊不记memory。）"})

        # 调用 LLM（带重试）。网络搜索由 LLM 通过 JSON 中的 search 字段按需触发
        llm = self.cfg.llm
        max_tok = mood.llm_max_tokens()
        payload = {
            "model": llm.model,
            "messages": messages,
            "temperature": mood.llm_temperature(),
            "max_tokens": max_tok,
        }

        try:
            full_reply = await _call_llm(llm.base_url, llm.api_key, payload)
        except Exception as e:
            logger.error(f"LLM 调用最终失败: {e}")
            return []

        # 解析 JSON（去掉 markdown 围栏）
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
                # 尝试修复截断的 JSON（补全缺失的括号）
                fixed = t.rstrip()
                open_braces = fixed.count("{") - fixed.count("}")
                if open_braces > 0:
                    fixed += "}" * open_braces
                try:
                    d = json.loads(fixed)
                    return d if isinstance(d, dict) else None
                except (json.JSONDecodeError, TypeError):
                    pass
                # 兜底：用正则提取 reply 字段
                m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', t)
                if m:
                    try:
                        return {"reply": json.loads(f'"{m.group(1)}"')}
                    except Exception:
                        pass
                return None

        data = _parse_json(full_reply)

        # 处理 memory
        def _save_memory(mem, uid):
            if not mem or not isinstance(mem, dict):
                return
            # 群聊中 LLM 可通过 user 字段指定记忆归属谁
            target_user = mem.get("user")
            if target_user and names_map and target_user in names_map:
                uid = names_map[target_user]
            action = mem.get("action", "save")
            cat = str(mem.get("category", "")).strip()
            title = str(mem.get("title", "")).strip()
            if not cat or not title:
                return
            try:
                if action == "delete":
                    self.memory.forget(uid, cat, title)
                else:
                    content = str(mem.get("content", "")).strip()
                    if content:
                        self.memory.remember(uid, cat, title, content)
            except Exception:
                pass

        # ---- 多步：say + search → 先返回思考消息，后台异步查+回复 ----
        if data and data.get("say") and not data.get("reply"):
            say_text = data.get("say", "")
            want_quote = data.get("quote", False)
            search_query = data.get("search", "")
            say_parts = self._split_reply(say_text) if say_text else []

            for part in say_parts:
                self._append_history(user_id, "assistant", part, group_id)

            # 后台任务：真正执行搜索 → LLM → 发送结果
            async def _followup():
                conv_key = self._conv_key(user_id, group_id)
                final_data = {}
                if search_query and self.cfg.web_search_enabled:
                    try:
                        results = await bing_search(str(search_query))
                        logger.info(f"[multi-step] search done: {len(results) if results else 0} results")
                        if results:
                            ctx = ("## 网络搜索结果\n" + format_search_results(results)
                                   + "\n\n用鱼自己的话把结果讲出来，绝对不能直接贴搜索结果的格式或文字。然后给出最终回复的JSON（包含reply和memory字段）。")
                            fu_msgs = list(messages)
                            fu_msgs.append({"role": "system", "content": ctx})
                            fu_payload = {
                                "model": llm.model, "messages": fu_msgs,
                                "temperature": mood.llm_temperature(),
                                "max_tokens": mood.llm_max_tokens(),
                            }
                            raw2 = await _call_llm(llm.base_url, llm.api_key, fu_payload, timeout=45)
                            final_data = _parse_json(raw2) or {}
                            logger.info(f"[multi-step] follow-up LLM done, reply={bool(final_data.get('reply'))}")
                    except Exception as e2:
                        logger.error(f"[multi-step] follow-up failed: {e2}")

                reply_txt = final_data.get("reply", "") if final_data else ""
                if not reply_txt or reply_txt.strip() == "[SKIP]":
                    return
                q = final_data.get("quote", False)
                is_g = bool(group_id)
                _save_memory(final_data.get("memory"), user_id)
                _save_memory(final_data.get("diary"), "__diary__")
                if is_g:
                    _save_memory(final_data.get("group_memory"), f"__group__{group_id}")
                remind_final = final_data.get("remind")
                if remind_final and isinstance(remind_final, dict):
                    self._save_remind(remind_final, user_id, group_id)
                relay_final = final_data.get("relay")
                if relay_final and isinstance(relay_final, dict) and is_sender_admin:
                    asyncio.create_task(self._relay_message(relay_final, user_id))

                from server.bot.onebot_handler import onebot_server
                client = onebot_server.get_client(self.bot_qq)
                if not client or not client.connected:
                    logger.warning(f"[multi-step] client not connected, dropping reply: {reply_txt[:50]}")
                    return
                logger.info(f"[multi-step] sending follow-up: {len(reply_txt)} chars")
                target = group_id if is_g else user_id
                for pi, part in enumerate(self._split_reply(reply_txt)):
                    try:
                        part = re.sub(r"^>>\s*", "", part)  # strip >>
                        qid = quote_msg_id if (q and pi == 0 and quote_msg_id) else None
                        if qid:
                            if is_g:
                                await client.send_group_msg_quote(target, part, qid)
                            else:
                                await client.send_private_msg_quote(user_id, part, qid)
                        else:
                            if is_g:
                                await client.send_group_msg(target, part)
                            else:
                                await client.send_private_msg(user_id, part)
                        if pi < len(self._split_reply(reply_txt)) - 1:
                            typing_delay = max(2.0, len(part) * 0.08 + 1.0)
                            await asyncio.sleep(typing_delay)
                    except Exception:
                        pass
                    self._append_history(user_id, "assistant", part, group_id)

            asyncio.create_task(_followup())

            result = []
            for i, part in enumerate(say_parts):
                qid = quote_msg_id if (want_quote and i == 0 and quote_msg_id) else None
                result.append(ReplyPart(part, qid))
            return result

        # ---- 简单回复 ----
        reply_text = ""
        want_quote = False
        if data:
            reply_text = data.get("reply", "")
            want_quote = data.get("quote", False)
            _save_memory(data.get("memory"), user_id)
            _save_memory(data.get("diary"), "__diary__")
            if is_group:
                _save_memory(data.get("group_memory"), f"__group__{group_id}")
            forget_info = data.get("forget")
            if forget_info and isinstance(forget_info, dict):
                _save_memory({**forget_info, "action": "delete"}, user_id)
            remind_info = data.get("remind")
            if remind_info and isinstance(remind_info, dict):
                self._save_remind(remind_info, user_id, group_id)
            relay_info = data.get("relay")
            if relay_info and isinstance(relay_info, dict) and is_sender_admin:
                asyncio.create_task(self._relay_message(relay_info, user_id))
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

        # JSON 解析失败时，异步提取记忆（兜底），短超时不影响回复
        if not data and reply_text and len(reply_text) > 10:
            try:
                await asyncio.wait_for(
                    self._fallback_memory(user_id, user_name, text, reply_text), timeout=10)
            except Exception:
                pass

        result = []
        for i, part in enumerate(self._split_reply(reply_text)):
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
        payload = {"model": llm.model, "messages": msgs, "temperature": 0.3, "max_tokens": 150}
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
            history = self._get_history("", group_id, max_len=8)
            ctx_lines = []
            for m in history[-6:]:
                role = "对方" if m.get("role") == "user" else "鱼"
                ctx_lines.append(f"{role}: {m.get('content', '')[:80]}")
            context = "\n".join(ctx_lines) if ctx_lines else "（暂无历史）"

            at_note = "（有人@鱼。但如果鱼在生气，或管理员在要求鱼SKIP/测试，可以不回。）" if mentioned else "（如果管理员要求鱼SKIP/测试，鱼应该配合不回。）"
            prompt = (
                f"{persona_brief}\n\n"
                "你是过滤器。鱼在群里非常安静，绝大多数时候都不说话。请严格判断她是否应该回复。\n"
                "回复 YES（极少）：消息极其有趣让鱼忍不住想插嘴、有人在认真求助需要帮忙、话题正是鲨鱼/海洋/睡觉/软绵绵/科技、或者是明确跟鱼说话且鱼没有生气。\n"
                "回复 NO（默认）：以上情况之外的一切——日常闲聊、寒暄、路人对话、不感兴趣、不确定跟鱼有没有关系、鱼在生这个人的气。\n"
                "拿不准一律 NO。鱼的回复非常珍贵，不能随便开口。\n"
                f"{at_note}\n"
                "只输出 YES 或 NO。"
            )
            payload = {
                "model": llm.model, "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"最近对话：\n{context}\n\n新消息：\n{text[:500]}"},
                ], "temperature": 0.3, "max_tokens": 5,
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

    async def _relay_message(self, relay: dict, from_user_id: str):
        """代传话给另一位管理员。仅管理员之间可用。"""
        try:
            to_role = str(relay.get("to_role", "")).strip()
            content = str(relay.get("content", "")).strip()
            if not to_role or not content:
                return
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
            from_label = f"【{from_role}】" if from_role else "管理员"
            relay_text = f"{from_label}让鱼转达：{content}"

            from server.bot.onebot_handler import onebot_server
            client = onebot_server.get_client(self.bot_qq)
            if client and client.connected:
                await client.send_private_msg(target_qq, relay_text)
                logger.info(f"[{self.bot_qq}] Relay: {from_user_id}({from_role}) -> {target_qq}({to_role})")
                # 推送到前端
                try:
                    from server.webui.routes import push_event
                    await push_event({
                        "type": "relay", "qq": self.bot_qq,
                        "from_user": from_user_id, "from_role": from_role,
                        "to_user": target_qq, "to_role": to_role,
                        "content": content,
                    })
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Relay failed: {e}")

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

    async def proactive_message(self, user_id: str, group_id: str = "") -> str | None:
        """Generate a proactive message. Returns text or None if SKIP/error."""
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

        prompt_text = PROACTIVE_PROMPT.format(context=context)

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
