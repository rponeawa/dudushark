"""
Proactive messaging — DuduShark occasionally initiates conversations driven by
her mood/sleep state and relationship warmth with each person.
"""

import asyncio
import json
import logging
import random
import re
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

# 检查周期：根据心情状态调整间隔
CYCLE_AWAKE   = (120, 300)    #  2–5 min — 清醒时频繁检查
CYCLE_SLEEPY  = (300, 900)    #  5–15 min — 犯困了降低频率
CYCLE_SLEEPING = (1200, 2400)  # 20–40 min — 睡着了基本不查


class ProactiveScheduler:
    """Per-instance scheduler. Dudu decides whether and whom to talk to."""

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

    # ── helpers ──────────────────────────────────────────────

    def _now(self) -> float:
        return time.time()

    @property
    def _cfg(self):
        return get_message_handler(self.bot_qq).cfg

    @staticmethod
    def _hour_utc8() -> int:
        from datetime import datetime, timezone, timedelta
        return datetime.now(timezone(timedelta(hours=8))).hour

    def _is_sleep_time(self) -> bool:
        """22:00-08:00 = core quiet hours."""
        h = self._hour_utc8()
        return h >= 22 or h < 8

    # ── sleep override (memory-based) ───────────────────────

    SLEEP_OVERRIDE_CATEGORY = "__proactive__"
    SLEEP_OVERRIDE_TITLE = "sleep_allow"

    def _has_sleep_override(self, user_id: str) -> bool:
        try:
            handler = get_message_handler(self.bot_qq)
            mems = handler.memory.recall_by_category(user_id, self.SLEEP_OVERRIDE_CATEGORY)
            return any(self.SLEEP_OVERRIDE_TITLE in m.get("text", "") for m in mems)
        except Exception:
            return False

    # ── decision: should I speak? ───────────────────────────

    def _should_speak_now(self) -> bool:
        """Single desire check: energy × curiosity, one random roll."""
        if not self._cfg.proactive_enabled:
            return False

        now = self._now()
        if now - self._last_global_ts < self._cfg.proactive_global_cooldown_sec:
            return False

        self.mood.update()

        # Sleeping: don't initiate (reminders still fire in _cycle)
        if self._is_sleep_time():
            return False

        # Desire = baseline curiosity × current energy (0–1)
        # Energy already reflects sleepy/awake via mood system
        desire = self._cfg.proactive_curiosity_threshold * self.mood.energy
        return random.random() < desire

    # ── decision: whom to talk to? ──────────────────────────

    def _user_messaged_today(self, conv_key: str, handler) -> bool:
        """对方今天（UTC+8 0:00 起）有没有给鱼发过消息。"""
        from datetime import datetime, timezone, timedelta
        tz8 = timezone(timedelta(hours=8))
        today_start = datetime.now(tz8).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        msgs = handler._conversations.get(conv_key, [])
        return any(m.get("role") == "user" and m.get("ts", 0) >= today_start for m in msgs)

    def _relationship_warmth(self, conv_key: str, handler) -> float:
        """0.0–1.0 score reflecting how close Dudu feels to this person.

        Based on total exchange count and recency.  More messages →
        warmer.  Long silence → cools down.
        """
        msgs = handler._conversations.get(conv_key, [])
        if not msgs:
            return 0.1

        exchanges = sum(1 for m in msgs if m.get("role") in ("user", "assistant"))
        last_ts = max((m.get("ts", 0) for m in msgs), default=0)
        age_hours = (self._now() - last_ts) / 3600

        # Frequent conversation = close bond
        bond = min(1.0, exchanges / 60)
        # Recency decay: still warm within 24h, fades over a week
        recency = max(0.1, 1.0 - age_hours / 168)
        return bond * recency

    def _pick_conversation(self) -> tuple[str, str, str] | None:
        """Return (user_id, group_id, conv_key) or None."""
        handler = get_message_handler(self.bot_qq)
        eligible = handler.get_eligible_conversations()
        if not eligible:
            return None

        now = self._now()
        candidates = []
        weights = []

        for conv_key, user_id, group_id, last_ts in eligible:
            # 对方今天没说过话 → 不主动找
            if not self._user_messaged_today(conv_key, handler):
                continue
            # Per-conversation cooldown
            if now - self._last_conv_proactive.get(conv_key, 0) < self._cfg.proactive_per_conv_cooldown_sec:
                continue

            # Sleep + no override → skip
            if self._is_sleep_time() and not self._has_sleep_override(user_id):
                continue

            # Base weight: private vs group
            w = self._cfg.proactive_private_probability if not group_id else self._cfg.proactive_group_probability

            # Relationship warmth bonus: closer people get priority
            w *= 0.5 + self._relationship_warmth(conv_key, handler)

            # Recency: very recent (≤30 min) gets a soft boost, very stale (>7 d) gets a penalty
            idle_minutes = (now - last_ts) / 60
            if idle_minutes < 30:
                w *= 1.5
            elif idle_minutes > 10080:  # >7 days
                w *= 0.3

            if w <= 0:
                continue
            candidates.append((conv_key, user_id, group_id))
            weights.append(w)

        if not candidates:
            return None

        # Weighted random pick
        total = sum(weights)
        r = random.random() * total
        cumulative = 0.0
        for i, w in enumerate(weights):
            cumulative += w
            if r <= cumulative:
                return candidates[i][1], candidates[i][2], candidates[i][0]

        return None

    # ── reminders ──────────────────────────────────────────

    async def _check_reminders(self):
        try:
            from server.config import get_reminders_path
            path = get_reminders_path(self.bot_qq)
            if not path.exists():
                return
            reminders = json.loads(path.read_text(encoding="utf-8"))
            if not reminders:
                return
            now = self._now()
            remaining = []
            for r in reminders:
                if r.get("at_utc", float("inf")) <= now:
                    await self._fire_reminder(r)
                else:
                    remaining.append(r)
            path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2))
        except Exception:
            pass

    async def _fire_reminder(self, r: dict):
        client = onebot_server.get_client(self.bot_qq)
        if not client or not client.connected:
            return
        content = r.get("content", "")
        user_id = r.get("user_id", "")
        try:
            if user_id:
                await client.send_private_msg(user_id, content)
            logger.info(f"[{self.bot_qq}] Reminder fired: to={user_id}")
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Reminder send failed: {e}")

    # ── main cycle ─────────────────────────────────────────

    async def _cycle(self):
        # Reminders always fire (unconditional)
        await self._check_reminders()

        if not self._should_speak_now():
            return

        picked = self._pick_conversation()
        if not picked:
            return

        user_id, group_id, conv_key = picked
        handler = get_message_handler(self.bot_qq)

        try:
            replies = await handler.proactive_message(user_id, group_id)
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Proactive LLM error: {e}")
            return

        if not replies:
            return

        client = onebot_server.get_client(self.bot_qq)
        if not client or not client.connected:
            return

        try:
            is_group = bool(group_id)
            target = group_id if is_group else user_id
            for i, part in enumerate(replies):
                text = re.sub(r"^>>\s*", "", part.text)
                if part.voice:
                    # 语音回复
                    import base64, os, uuid
                    audio_bytes = await handler._tts_speak(text, part.voice_emotion)
                    if audio_bytes:
                        tts_dir = handler.cfg.tts_host_dir or os.path.join(os.path.expanduser("~"), "napcat/config/tts")
                        os.makedirs(tts_dir, exist_ok=True)
                        fname = f"{uuid.uuid4().hex[:8]}.wav"
                        with open(os.path.join(tts_dir, fname), "wb") as f:
                            f.write(audio_bytes)
                        docker_path = f"/app/napcat/config/tts/{fname}"
                        if is_group:
                            await client.send_group_voice(target, docker_path)
                        else:
                            await client.send_private_voice(user_id, docker_path)
                    else:
                        if is_group:
                            await client.send_group_msg(target, text)
                        else:
                            await client.send_private_msg(user_id, text)
                else:
                    if is_group:
                        await client.send_group_msg(target, text)
                    else:
                        await client.send_private_msg(user_id, text)
                if i < len(replies) - 1:
                    await asyncio.sleep(max(2.0, len(text) * 0.08 + 1.0))

            now = self._now()
            self._last_global_ts = now
            self._last_conv_proactive[conv_key] = now
            logger.info(f"[{self.bot_qq}] Proactive sent → {conv_key} ({len(replies)} parts)")
        except Exception as e:
            logger.error(f"[{self.bot_qq}] Proactive send failed: {e}")

    # ── timing ─────────────────────────────────────────────

    def _next_delay(self) -> float:
        """Seconds until next cycle. Shorter when awake, longer when sleepy."""
        state = self.mood.sleep_state
        if state == "sleeping":
            lo, hi = CYCLE_SLEEPING
        elif state == "sleepy":
            lo, hi = CYCLE_SLEEPY
        else:
            lo, hi = CYCLE_AWAKE
        return random.uniform(lo, hi)

    async def _loop(self):
        while not self._stopped:
            try:
                await self._cycle()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception(f"[{self.bot_qq}] Proactive cycle error")
            try:
                await asyncio.sleep(self._next_delay())
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
