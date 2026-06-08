"""
DuduShark sticker collection — save liked stickers with vector search.
"""

import json
import time
from pathlib import Path


class StickerLibrary:
    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self.stickers: list[dict] = []
        self._vs = None
        self._load()

    def _path(self) -> Path:
        from server.config import get_instance_dir
        return get_instance_dir(self.bot_qq) / "stickers.json"

    def _get_vs(self):
        if self._vs is None:
            from server.config import get_chroma_dir
            from server.memory.vector_store import VectorStore
            chroma_dir = get_chroma_dir(self.bot_qq)
            self._vs = VectorStore(chroma_dir, "__stickers__")
        return self._vs

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
        for s in self.stickers:
            if s.get("url") == url:
                return None
        sid = len(self.stickers) + 1
        entry = {
            "id": sid, "url": url, "description": description,
            "tags": tags or [], "saved_at": time.time(), "used_count": 0,
        }
        self.stickers.append(entry)
        self._save()
        # 写入向量索引
        try:
            vs = self._get_vs()
            text = f"{description} {' '.join(tags or [])}"
            vs.add(str(sid), text, {"id": sid})
        except Exception:
            pass
        return entry

    def existing_urls(self) -> list[str]:
        return [s["url"] for s in self.stickers]

    def existing_summary(self) -> str:
        if not self.stickers:
            return ""
        lines = ["已收藏的表情包:"]
        for s in self.stickers[-20:]:
            lines.append(f"  [{s['id']}] {s['description']} — {', '.join(s.get('tags',[]))}")
        return "\n".join(lines)

    def search(self, query: str, n: int = 5, min_score: float = 0.4) -> list[dict]:
        """Vector search with similarity threshold. Below threshold = no match."""
        if not query or not self.stickers:
            return []
        results = []
        try:
            vs = self._get_vs()
            raw = vs.search(query, max(n * 2, 10))
            seen = set()
            for r in raw:
                score = r.get("score", 0)
                if score < min_score:
                    continue
                sid = r.get("meta", {}).get("id") if r.get("meta") else None
                if sid is None:
                    sid = int(r["id"]) if r["id"].isdigit() else None
                if sid and sid not in seen:
                    seen.add(sid)
                    for s in self.stickers:
                        if s["id"] == sid:
                            results.append(s)
                            break
                if len(results) >= n:
                    break
        except Exception:
            pass
        return results

    def mark_used(self, sticker_id: int):
        for s in self.stickers:
            if s["id"] == sticker_id:
                s["used_count"] = s.get("used_count", 0) + 1
                self._save()
                break

    def remove(self, sticker_id: int) -> bool:
        for s in self.stickers:
            if s["id"] == sticker_id:
                self.stickers.remove(s)
                self._save()
                try:
                    self._get_vs().delete(str(sticker_id))
                except Exception:
                    pass
                return True
        return False

    def get_all(self) -> list[dict]:
        return list(self.stickers)

    def count(self) -> int:
        return len(self.stickers)


_libraries: dict[str, StickerLibrary] = {}


def get_sticker_library(bot_qq: str) -> StickerLibrary:
    if bot_qq not in _libraries:
        _libraries[bot_qq] = StickerLibrary(bot_qq)
    return _libraries[bot_qq]
