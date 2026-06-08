"""
DuduShark sticker collection — save liked stickers with descriptions.
Dudu can save stickers she likes and occasionally send them.
"""

import json
import time
from pathlib import Path


class StickerLibrary:
    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self.stickers: list[dict] = []
        self._load()

    def _path(self) -> Path:
        from server.config import get_instance_dir
        return get_instance_dir(self.bot_qq) / "stickers.json"

    def _load(self):
        p = self._path()
        if p.exists():
            try:
                self.stickers = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                self.stickers = []

    def _save(self):
        p = self._path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.stickers, ensure_ascii=False, indent=2))

    def add(self, url: str, description: str, tags: list[str] | None = None) -> dict | None:
        """Save a sticker Dudu likes. Deduplicates by URL. Returns the saved entry or None if duplicate."""
        # URL 去重
        for s in self.stickers:
            if s.get("url") == url:
                return None
        entry = {
            "id": len(self.stickers) + 1,
            "url": url,
            "description": description,
            "tags": tags or [],
            "saved_at": time.time(),
            "used_count": 0,
        }
        self.stickers.append(entry)
        self._save()
        return entry

    def existing_urls(self) -> list[str]:
        """Return list of already-saved sticker URLs for prompt injection."""
        return [s["url"] for s in self.stickers]

    def existing_summary(self) -> str:
        """Brief summary of saved stickers for LLM prompt."""
        if not self.stickers:
            return ""
        lines = ["已收藏的表情包:"]
        for s in self.stickers[-20:]:
            lines.append(f"  [{s['id']}] {s['description']} — {', '.join(s.get('tags',[]))}")
        return "\n".join(lines)

    def search(self, query: str, n: int = 5) -> list[dict]:
        """Simple keyword search in descriptions and tags."""
        if not query:
            return self.stickers[-n:]
        q = query.lower()
        matches = []
        for s in self.stickers:
            score = 0
            if q in s.get("description", "").lower():
                score += 2
            for t in s.get("tags", []):
                if q in t.lower():
                    score += 1
            if score > 0:
                matches.append((score, s))
        matches.sort(key=lambda x: -x[0])
        return [m[1] for m in matches[:n]]

    def mark_used(self, sticker_id: int):
        for s in self.stickers:
            if s["id"] == sticker_id:
                s["used_count"] = s.get("used_count", 0) + 1
                self._save()
                break

    def get_all(self) -> list[dict]:
        return list(self.stickers)

    def count(self) -> int:
        return len(self.stickers)


_libraries: dict[str, StickerLibrary] = {}


def get_sticker_library(bot_qq: str) -> StickerLibrary:
    if bot_qq not in _libraries:
        _libraries[bot_qq] = StickerLibrary(bot_qq)
    return _libraries[bot_qq]
