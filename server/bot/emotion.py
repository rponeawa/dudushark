"""
DuduShark emotion system — separate from sleep/energy mood.
Tracks emotional state with smooth transitions, injected into prompts.
"""

import json
import time
from pathlib import Path

DEFAULT_EMOTIONS = {
    "开心": 0.3,
    "生气": 0.0,
    "难过": 0.0,
    "兴奋": 0.2,
    "撒娇": 0.2,
    "平静": 0.5,
    "困惑": 0.0,
    "傲娇": 0.3,
}

SMOOTH_FACTOR = 0.35  # 每次更新：新值 = 旧值 * (1-factor) + 变化 * factor


class DuduEmotion:
    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self.values: dict[str, float] = dict(DEFAULT_EMOTIONS)
        self._load()

    def _path(self) -> Path:
        from server.config import get_instance_dir
        return get_instance_dir(self.bot_qq) / "emotion_state.json"

    def _load(self):
        p = self._path()
        if p.exists():
            try:
                saved = json.loads(p.read_text(encoding="utf-8"))
                for k, v in saved.get("values", {}).items():
                    if k in DEFAULT_EMOTIONS:
                        self.values[k] = max(0.0, min(1.0, float(v)))
            except Exception:
                pass

    def _save(self):
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"values": self.values}, ensure_ascii=False, indent=2))

    def update(self, changes: dict[str, float]):
        """Apply emotion changes. Positive=increase, negative=decrease."""
        for name, delta in changes.items():
            if name not in self.values:
                continue
            delta = max(-1.0, min(1.0, float(delta)))
            self.values[name] = max(0.0, min(1.0,
                self.values[name] * (1 - SMOOTH_FACTOR) +
                max(0.0, self.values[name] + delta) * SMOOTH_FACTOR
            ))
        self._save()

    def dominant(self) -> str:
        """Return the most intense emotion name."""
        if not self.values:
            return "平静"
        return max(self.values, key=lambda k: self.values[k])

    def context(self) -> str:
        """Return a paragraph for system prompt injection."""
        parts = []
        active = [(k, v) for k, v in self.values.items() if v > 0.15]
        active.sort(key=lambda x: -x[1])
        if not active:
            return "当前情绪: 平静 (100%)"
        lines = [f"当前情绪:"]
        for name, val in active[:4]:
            pct = round(val * 100)
            lines.append(f"  {name}: {pct}%")
        return "\n".join(lines)

    def state_dict(self) -> dict:
        return {
            "values": {k: round(v, 3) for k, v in self.values.items()},
            "dominant": self.dominant(),
        }


_emotions: dict[str, DuduEmotion] = {}


def get_emotion(bot_qq: str) -> DuduEmotion:
    if bot_qq not in _emotions:
        _emotions[bot_qq] = DuduEmotion(bot_qq)
    return _emotions[bot_qq]
