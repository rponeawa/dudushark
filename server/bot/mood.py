"""
Global mood & sleep system for DuduShark.
One DuduMood instance per bot QQ, shared across proactive scheduler and message handler.
The hourly curve is a baseline — Dudu has her own will and can deviate from it.
"""

import random
import time
from datetime import datetime, timezone

_HOURLY_BASELINE = {
    0: 0.02, 1: 0.01, 2: 0.01, 3: 0.01, 4: 0.02, 5: 0.05,
    6: 0.08, 7: 0.18, 8: 0.42, 9: 0.58, 10: 0.62, 11: 0.58,
    12: 0.48, 13: 0.42, 14: 0.48, 15: 0.52, 16: 0.58, 17: 0.62,
    18: 0.72, 19: 0.82, 20: 0.72, 21: 0.30, 22: 0.12, 23: 0.05,
}

_HOURLY_FLAVOR = {
    (0, 8): "夜深了，你睡得正香，被吵醒了也迷迷糊糊的。回复要非常简短，不想说话。",
    (8, 10): "天刚亮不久。你刚刚迷迷糊糊地醒来，还有点没睡醒的感觉。说话可以带点迷糊和软绵绵的感觉。",
    (10, 21): "现在是白天，你精神不错。说话可以比平时活泼一些。到了晚上反而最精神。",
    (21, 23): "夜深了，你开始犯困了。说话会变得简短慵懒。",
    (23, 24): "深夜了，你已经睡着了。被吵醒的话迷迷糊糊的。",
}

_SLEEP_FLAVOR = {
    "awake": "",
    "sleepy": "你有点困了，说话会变得简短慵懒，反应慢半拍。",
    "just_woke": "你刚睡醒，还有点迷迷糊糊的，说话带点慵懒和软绵绵的感觉。",
    "sleeping": "你正在睡觉，被吵醒了也迷迷糊糊的。回复要非常非常简短，不想说话。",
}


class DuduMood:
    """Per-instance mood/sleep state shared across all subsystems."""

    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self.sleep_state = "awake"
        self.sleep_state_until: float = 0.0
        self.hourly_mood: float = 0.5
        self.energy: float = 0.5
        self._last_update: float = 0.0
        self._mood_offset: float = 0.0
        self._offset_until: float = 0.0

    # ---- public API ----

    def update(self):
        """Tick the mood state. Call at the start of any interaction or cycle."""
        now = time.time()
        if self.hourly_mood == 0.5:  # first update
            self._full_update(now)
        elif now - self._last_update > 30:  # throttle to once per ~30s
            self._full_update(now)

    def chattiness(self) -> float:
        """How likely Dudu is to speak (0.0–1.0+). Used by proactive scheduler."""
        return self.energy

    def llm_temperature(self, base: float = 0.85) -> float:
        return base

    def llm_max_tokens(self, base: int = 4096) -> int:
        if self.sleep_state == "sleepy":
            return max(1024, base // 2)
        return base

    def system_mood_context(self) -> str:
        """Returns a paragraph describing current mood to inject into system prompt."""
        parts = []

        hour = self._hour()
        for (lo, hi), text in _HOURLY_FLAVOR.items():
            if lo <= hour < hi:
                parts.append(text)
                break

        flavor = _SLEEP_FLAVOR.get(self.sleep_state, "")
        if flavor:
            parts.append(flavor)

        # 注入精力值，让 LLM 感知自己的状态
        parts.append(f"当前精力: {round(self.energy * 100)}%")

        return "\n".join(parts)

    def state_dict(self) -> dict:
        """Snapshot for API/frontend."""
        return {
            "sleep_state": self.sleep_state,
            "hourly_mood": round(self.hourly_mood, 3),
            "energy": round(self.energy, 3),
        }

    # ---- internal ----

    @staticmethod
    def _now() -> float:
        return time.time()

    @staticmethod
    def _hour() -> int:
        tz = timezone(__import__("datetime").timedelta(hours=8))
        return datetime.now(tz).hour

    def _full_update(self, now: float):
        self._last_update = now
        hour = self._hour()
        # 23-8 固定睡着，21-23 固定犯困
        if hour >= 23 or hour < 8:
            self.sleep_state = "sleeping"
            self.sleep_state_until = now + 3600
            self.energy = 0.05
            return
        if 21 <= hour < 23:
            self.sleep_state = "sleepy"
            self.sleep_state_until = now + 1800
            self.energy = 0.08
            return
        self._refresh_offset(now)
        self._tick_sleep(now)

        hour = self._hour()
        baseline = _HOURLY_BASELINE.get(hour, 0.30)

        # Dudu can randomly deviate from the baseline
        self.hourly_mood = max(0.0, min(1.0, baseline + self._mood_offset))
        sleep_mod = self._sleep_modifier()
        self.energy = min(1.0, self.hourly_mood * sleep_mod)

    def _refresh_offset(self, now: float):
        """Every 2-6 hours, Dudu re-decides how she feels about the current time."""
        if now < self._offset_until:
            return
        # Normal drift: vary ±0.15 around baseline
        self._mood_offset = random.uniform(-0.15, 0.15)
        self._offset_until = now + random.randint(7200, 14400)

    def _tick_sleep(self, now: float):
        # 过了 8 点还没醒 → 强制醒来
        if self.sleep_state == "sleeping":
            if self._hour() >= 8:
                self.sleep_state = "just_woke"
                self.sleep_state_until = now + random.randint(180, 480)
            return

        if now < self.sleep_state_until:
            return

        if self.sleep_state == "awake":
            if random.random() < 0.10:
                self.sleep_state = "sleepy"
                self.sleep_state_until = now + random.randint(300, 1800)
        elif self.sleep_state == "sleepy":
            self.sleep_state = "just_woke"
            self.sleep_state_until = now + random.randint(180, 480)
        elif self.sleep_state == "just_woke":
            self.sleep_state = "awake"
            self.sleep_state_until = now + random.randint(3600, 7200)

    def _sleep_modifier(self) -> float:
        if self.sleep_state == "sleeping":
            return 0.02
        if self.sleep_state == "sleepy":
            return 0.25
        if self.sleep_state == "just_woke":
            return 2.0
        return 1.0


_moods: dict[str, DuduMood] = {}


def get_mood(bot_qq: str) -> DuduMood:
    if bot_qq not in _moods:
        _moods[bot_qq] = DuduMood(bot_qq)
    return _moods[bot_qq]


def remove_mood(bot_qq: str):
    _moods.pop(bot_qq, None)
