"""
DuduShark sticker collection — save liked stickers with vector search.
Images are downloaded and stored locally since QQ URLs expire.
"""

import asyncio
import json
import os
import time
from pathlib import Path


def _sticker_dir(bot_qq: str) -> Path:
    from server.config import get_instance_dir
    p = get_instance_dir(bot_qq) / "stickers"
    p.mkdir(parents=True, exist_ok=True)
    return p


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

    async def add(self, url: str, description: str, tags: list[str] | None = None) -> dict | None:
        """Download and save sticker locally. Deduplicates by URL. Async."""
        for s in self.stickers:
            if s.get("url") == url:
                return None
        sid = len(self.stickers) + 1
        # 下载图片落盘
        fname = f"{sid}_{int(time.time())}.gif"
        fpath = _sticker_dir(self.bot_qq) / fname
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    fpath.write_bytes(r.content)
        except Exception:
            fname = ""  # 下载失败不存文件，但记录仍保留

        entry = {
            "id": sid, "url": url, "file": fname,
            "description": description, "tags": tags or [],
            "saved_at": time.time(), "used_count": 0,
        }
        self.stickers.append(entry)
        self._save()
        try:
            vs = self._get_vs()
            text = f"{description} {' '.join(tags or [])}"
            vs.add(str(sid), text, {"id": sid})
        except Exception:
            pass
        return entry

    def add_sync(self, url: str, description: str, tags: list[str] | None = None) -> dict | None:
        """Sync wrapper for add()."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        return loop.run_until_complete(self.add(url, description, tags))

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

    def get_path(self, sticker_id: int) -> Path | None:
        for s in self.stickers:
            if s["id"] == sticker_id and s.get("file"):
                p = _sticker_dir(self.bot_qq) / s["file"]
                if p.exists():
                    return p
        return None

    def mark_used(self, sticker_id: int):
        for s in self.stickers:
            if s["id"] == sticker_id:
                s["used_count"] = s.get("used_count", 0) + 1
                self._save()
                break

    def remove(self, sticker_id: int) -> bool:
        for s in self.stickers:
            if s["id"] == sticker_id:
                # 删除落盘文件
                if s.get("file"):
                    f = _sticker_dir(self.bot_qq) / s["file"]
                    if f.exists():
                        f.unlink()
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
