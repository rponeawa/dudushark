"""
DuduShark emotion system — single dominant emotion with intensity.
LLM outputs emotion name, system manages smooth transitions.
"""

import json
from pathlib import Path

EMOTIONS = ["开心", "生气", "难过", "兴奋", "撒娇", "平静", "困惑", "傲娇"]

TRANSITION_SPEED = 0.65   # 每次 tick 过渡进度，约 2 次完成
DEFAULT_INTENSITY = 0.6   # 新情绪的默认强度


class DuduEmotion:
    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self.current = "平静"
        self.intensity = 0.3
        self._from_intensity: float = 0.3
        self._progress: float = 1.0  # 1.0 = no transition
        self._load()

    def _path(self) -> Path:
        from server.config import get_instance_dir
        return get_instance_dir(self.bot_qq) / "emotion_state.json"

    def _load(self):
        p = self._path()
        if p.exists():
            try:
                saved = json.loads(p.read_text(encoding="utf-8"))
                self.current = saved.get("current", "平静")
                self.intensity = max(0.0, min(1.0, float(saved.get("intensity", 0.3))))
            except Exception:
                pass

    def _save(self):
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "current": self.current, "intensity": round(self.intensity, 3),
        }, ensure_ascii=False, indent=2))

    def set_emotion(self, name: str | None):
        """LLM outputs an emotion name. Start transition from current → new."""
        if not name or name not in EMOTIONS or name == self.current:
            return
        self._from_intensity = self.intensity
        self.current = name
        self.intensity = DEFAULT_INTENSITY
        self._progress = 0.0

    def tick(self):
        """Advance transition and apply natural drift toward 平静."""
        if self._progress < 1.0:
            self._progress += TRANSITION_SPEED
            if self._progress >= 1.0:
                self._progress = 1.0
        # Natural drift: intensity slowly moves toward 0.3 (baseline)
        self.intensity += (0.3 - self.intensity) * 0.05
        self.intensity = max(0.05, min(1.0, self.intensity))
        self._save()

    def context(self) -> str:
        pct = round(self.intensity * 100)
        return f"当前情绪: {self.current} ({pct}%)"

    def state_dict(self) -> dict:
        return {
            "current": self.current,
            "intensity": round(self.intensity, 3),
        }


_emotions: dict[str, DuduEmotion] = {}


def get_emotion(bot_qq: str) -> DuduEmotion:
    if bot_qq not in _emotions:
        _emotions[bot_qq] = DuduEmotion(bot_qq)
    return _emotions[bot_qq]
