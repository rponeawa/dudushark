"""
Proactive messaging — DuduShark occasionally initiates conversations on her own,
driven by her personality and the global mood/sleep system.
Only in conversations where she has previously spoken.
"""

import asyncio
import logging
import random
import time

from server.bot.onebot_handler import onebot_server
from server.bot.message_handler import get_message_handler
from server.bot.mood import get_mood

logger = logging.getLogger("dudushark.proactive")

PROACTIVE_PROMPT = """你现在有一个属于自己的安静时刻。你是嘟嘟鲨鱼——来自鲨鱼星的赛博大鲨鱼。

你可以选择主动说点什么，也可以保持安静。这完全随你心意。

## 你可以做的事
- 想起某个朋友，问问他们最近怎么样
- 分享一个刚刚冒出来的想法或感受
- 好奇群里小伙伴们在聊什么
- 想找人陪你说说话
- 或者什么都不想做，安安静静地待着

## 规则
- 不想说话就输出 [SKIP]
- 自然一点，不要刻意找话题
- 简短就好，不用长篇大论
- 记住你是傲娇的但也温暖的
- 用"鱼"自称，口头禅"啊呜～"可以用但不要滥用

## 最近和这个人说了什么
{context}

现在，你想说点什么吗？"""

WAKE_ENGAGED_MIN = 180     # 3 min — Dudu最近在聊天
WAKE_ENGAGED_MAX = 480     # 8 min
WAKE_IDLE_MIN = 900        # 15 min — 有人说话但Dudu没参与
WAKE_IDLE_MAX = 2700       # 45 min
WAKE_QUIET_MIN = 1800      # 30 min — 完全安静
WAKE_QUIET_MAX = 3600      # 60 min


class ProactiveScheduler:
    """Per-instance scheduler that occasionally prompts Dudu to initiate."""

    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._last_global_ts: float = 0.0
        self._last_conv_proactive: dict[str, float] = {}
        self.mood = get_mood(bot_qq)

    def start(self):
        if self._task is None or self._task.done():
            self._stopped = False
            self._task = asyncio.create_task(self._loop())
            logger.info(f"[{self.bot_qq}] Proactive scheduler started")

    def stop(self):
        self._stopped = True
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

    # ---- internal helpers ----

    def _now(self) -> float:
        return time.time()

    @property
    def _cfg(self):
        return get_message_handler(self.bot_qq).cfg

    def _is_sleep_time(self) -> bool:
        """UTC+8: 22:00-08:00 为睡眠时段，不主动发言。"""
        from datetime import datetime, timezone, timedelta
        tz = timezone(timedelta(hours=8))
        hour = datetime.now(tz).hour
        return hour >= 22 or hour < 8

    # 允许睡眠时段主动联系的 memory 标记
    SLEEP_OVERRIDE_CATEGORY = "__proactive__"
    SLEEP_OVERRIDE_TITLE = "sleep_allow"

    def _has_sleep_override(self, user_id: str) -> bool:
        """检查用户是否明确要求过嘟嘟在睡眠时间主动找TA。"""
        try:
            handler = get_message_handler(self.bot_qq)
            mems = handler.memory.recall_by_category(user_id, self.SLEEP_OVERRIDE_CATEGORY)
            return any(self.SLEEP_OVERRIDE_TITLE in m.get("text", "") for m in mems)
        except Exception:
            return False

    def _should_speak_now(self) -> bool:
        if not self._cfg.proactive_enabled:
            return False

        now = self._now()
        if now - self._last_global_ts < self._cfg.proactive_global_cooldown_sec:
            return False

        self.mood.update()

        if not (random.random() < self._cfg.proactive_curiosity_threshold):
            return False

        return random.random() < self.mood.chattiness()

    def _pick_conversation(self) -> tuple[str, str, str] | None:
        """Return (user_id, group_id, conv_key) of chosen conversation, or None."""
        handler = get_message_handler(self.bot_qq)
        eligible = handler.get_eligible_conversations()
        if not eligible:
            return None

        in_sleep = self._is_sleep_time()
        now = self._now()
        candidates = []
        weights = []

        for conv_key, user_id, group_id, last_ts in eligible:
            # Per-conversation cooldown
            last_pro = self._last_conv_proactive.get(conv_key, 0)
            if now - last_pro < self._cfg.proactive_per_conv_cooldown_sec:
                continue

            # 睡眠时段只联系明确允许打扰的用户
            if in_sleep and not self._has_sleep_override(user_id):
                continue

            w = self._cfg.proactive_private_probability if not group_id else self._cfg.proactive_group_probability

            # Recency bonus: conversations with recent activity get a boost
            idle_minutes = (now - last_ts) / 60
            if idle_minutes < 30:
                w *= 2.0
            elif idle_minutes < 120:
                w *= 1.3
            elif idle_minutes > 1440:  # >24h idle
                w *= 0.3

            if w <= 0:
                continue
            candidates.append((conv_key, user_id, group_id))
            weights.append(w)

        if not candidates:
            return None

        # Weighted random choice
        total = sum(weights)
        r = random.random() * total
        cumulative = 0.0
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                conv_key, user_id, group_id = candidates[i]
                return user_id, group_id, conv_key

        return None

    async def _cycle(self):
        if not self._should_speak_now():
            return

        picked = self._pick_conversation()
        if not picked:
            return

        user_id, group_id, conv_key = picked
        handler = get_message_handler(self.bot_qq)

        try:
            text = await handler.proactive_message(user_id, group_id)
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Proactive LLM error: {e}")
            return

        if not text:
            return

        client = onebot_server.get_client(self.bot_qq)
        if not client or not client.connected:
            return

        try:
            is_group = bool(group_id)
            target = group_id if is_group else user_id
            if is_group:
                await client.send_group_msg(target, text)
            else:
                await client.send_private_msg(user_id, text)

            now = self._now()
            self._last_global_ts = now
            self._last_conv_proactive[conv_key] = now
            logger.info(f"[{self.bot_qq}] Proactive message sent to {conv_key}")
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Failed to send proactive message: {e}")

    def _conversation_engagement(self) -> str:
        """Check if Dudu is in active conversation. Returns 'engaged'|'idle'|'quiet'."""
        handler = get_message_handler(self.bot_qq)
        now = self._now()
        dudu_recent = False
        anyone_recent = False
        for conv_key in handler.list_conversations():
            msgs = handler._conversations.get(conv_key, [])
            for m in reversed(msgs):
                age = now - m.get("ts", 0)
                if age > 600:  # only look at last 10 min
                    break
                if m.get("role") == "assistant":
                    dudu_recent = True
                anyone_recent = True
                break  # found recent message
            if dudu_recent:
                break
        if dudu_recent:
            return "engaged"
        if anyone_recent:
            return "idle"
        return "quiet"

    def _next_wake(self) -> float:
        level = self._conversation_engagement()
        if level == "engaged":
            base = random.uniform(WAKE_ENGAGED_MIN, WAKE_ENGAGED_MAX)
        elif level == "idle":
            base = random.uniform(WAKE_IDLE_MIN, WAKE_IDLE_MAX)
        else:
            base = random.uniform(WAKE_QUIET_MIN, WAKE_QUIET_MAX)

        state = self.mood.sleep_state
        if state == "sleepy":
            base *= 2.5
        elif state in ("just_woke", "night_owl"):
            base *= 0.5
        return base

    async def _loop(self):
        while not self._stopped:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(f"[{self.bot_qq}] Proactive cycle error")
            try:
                await asyncio.sleep(self._next_wake())
            except asyncio.CancelledError:
                return


_schedulers: dict[str, ProactiveScheduler] = {}


def start_scheduler(bot_qq: str):
    if bot_qq not in _schedulers:
        _schedulers[bot_qq] = ProactiveScheduler(bot_qq)
    _schedulers[bot_qq].start()


def stop_scheduler(bot_qq: str):
    sched = _schedulers.pop(bot_qq, None)
    if sched:
        sched.stop()
