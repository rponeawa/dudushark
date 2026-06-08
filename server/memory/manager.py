"""
记忆管理器 — 为每个 QQ 用户建立独立的记忆文件夹，管理 MD 文件的 CRUD。
同时与 ChromaDB 向量存储同步。
"""

import re
import json
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from server.config import DATA_DIR, get_memory_dir, get_chroma_dir
from server.memory.vector_store import VectorStore

_CN_TZ = timezone(timedelta(hours=8))


def _utc_to_cn(text: str) -> str:
    """将记忆文本中的 UTC 时间转换为北京时间。"""
    return re.sub(
        r"时间: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z",
        lambda m: "时间: " + (
            datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
            .replace(tzinfo=timezone.utc)
            .astimezone(_CN_TZ)
            .strftime("%Y-%m-%d %H:%M")
        ),
        text,
    )


class MemoryManager:
    """管理某个 QQ 实例下的所有用户记忆。"""

    def __init__(self, bot_qq: str):
        self.bot_qq = bot_qq
        self._vector_stores: dict[str, VectorStore] = {}

    def _get_user_dir(self, user_id: str) -> Path:
        return get_memory_dir(self.bot_qq, user_id)

    def _get_vs(self, user_id: str) -> VectorStore:
        if user_id not in self._vector_stores:
            chroma_dir = get_chroma_dir(self.bot_qq)
            self._vector_stores[user_id] = VectorStore(chroma_dir, user_id)
        return self._vector_stores[user_id]

    def _make_entry_id(self, user_id: str, category: str, title: str) -> str:
        raw = f"{user_id}:{category}:{title}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _entry_file(self, user_id: str, category: str, title: str) -> Path:
        safe = re.sub(r"[^\w\-]", "_", title)
        return self._get_user_dir(user_id) / f"{category}_{safe}.md"

    # ---- 写入 ----

    def remember(self, user_id: str, category: str, title: str, content: str) -> bool:
        """写入或更新一条记忆，同步向量索引。返回 True=新建, False=更新。"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry_id = self._make_entry_id(user_id, category, title)
        filepath = self._entry_file(user_id, category, title)
        is_new = not filepath.exists()

        # 如果已存在且内容相同，只刷新日期
        if not is_new:
            try:
                existing = filepath.read_text(encoding="utf-8")
                # 提取旧内容（跳过元数据头部）
                old_body = existing.split("\n\n", 2)
                if len(old_body) >= 3 and old_body[2].strip() == content.strip():
                    # 内容未变，只更新时间
                    updated = re.sub(r"时间: .+", f"时间: {now}", existing)
                    filepath.write_text(updated, encoding="utf-8")
                    try:
                        vs = self._get_vs(user_id)
                        vs.add(entry_id, updated, {"category": category, "title": title, "date": now, "user": user_id})
                    except Exception:
                        pass
                    return False
            except Exception:
                pass

        full_text = f"# {title}\n\n> 类型: {category}\n> 时间: {now}\n> ID: {entry_id}\n\n{content}"
        filepath.write_text(full_text, encoding="utf-8")

        try:
            vs = self._get_vs(user_id)
            vs.add(entry_id, full_text, {"category": category, "title": title, "date": now, "user": user_id})
        except Exception:
            pass
        return is_new

    # ---- 读取 ----

    def recall_by_vector(self, user_id: str, query: str, n: int = 10) -> list[dict]:
        """向量检索记忆。返回时时间转为北京时间。"""
        try:
            vs = self._get_vs(user_id)
            results = vs.search(query, n)
            return [
                {"id": r["id"], "text": _utc_to_cn(r["text"]), "score": r["score"], "meta": r["meta"]}
                for r in results
            ]
        except Exception:
            return []

    def recall_by_category(self, user_id: str, category: str) -> list[dict]:
        """按分类列出记忆。返回时时间转为北京时间。"""
        user_dir = self._read_user_dir(user_id)
        if not user_dir:
            return []
        memories = []
        for f in sorted(user_dir.glob(f"{category}_*.md")):
            text = f.read_text(encoding="utf-8")
            memories.append({"file": str(f), "text": _utc_to_cn(text)})
        return memories

    def _read_user_dir(self, user_id: str) -> Path | None:
        """Return user dir if it exists, without creating it."""
        p = DATA_DIR / "instances" / self.bot_qq / "memories" / user_id
        return p if p.is_dir() else None

    def recall_all(self, user_id: str) -> list[dict]:
        """列出该用户所有记忆。返回时时间转为北京时间。"""
        user_dir = self._read_user_dir(user_id)
        if not user_dir:
            return []
        memories = []
        for f in sorted(user_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            memories.append({"file": f.name, "text": _utc_to_cn(text)})
        return memories

    def recall_by_date(self, user_id: str, date_str: str) -> list[dict]:
        """按日期查找记忆。date_str 格式 YYYY-MM-DD。返回时间转为北京时间。"""
        user_dir = self._read_user_dir(user_id)
        if not user_dir:
            return []
        memories = []
        for f in sorted(user_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            if date_str in text:
                memories.append({"file": f.name, "text": _utc_to_cn(text)})
        return memories

    def recall_recent(self, user_id: str, days: int = 7) -> list[dict]:
        """获取最近 N 天的记忆。"""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        user_dir = self._read_user_dir(user_id)
        if not user_dir:
            return []
        memories = []
        for f in sorted(user_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            m = re.search(r"时间: (\d{4}-\d{2}-\d{2})", text)
            if m and m.group(1) >= cutoff:
                memories.append({"file": f.name, "text": _utc_to_cn(text)})
        return memories

    # ---- 删除 ----

    def forget(self, user_id: str, category: str, title: str):
        """删除一条记忆。"""
        filepath = self._entry_file(user_id, category, title)
        if filepath.exists():
            filepath.unlink()
        entry_id = self._make_entry_id(user_id, category, title)
        try:
            vs = self._get_vs(user_id)
            vs.delete(entry_id)
        except Exception:
            pass

    def forget_all(self, user_id: str):
        """清空用户所有记忆。"""
        user_dir = self._get_user_dir(user_id)
        for f in user_dir.glob("*.md"):
            f.unlink()
        if user_id in self._vector_stores:
            try:
                self._vector_stores[user_id].clear()
            except Exception:
                pass
            del self._vector_stores[user_id]

    # ---- 辅助 ----

    def list_users(self) -> list[str]:
        """列出所有有记忆的用户 ID。"""
        inst_mem_dir = get_memory_dir(self.bot_qq, "")
        if not inst_mem_dir.exists():
            return []
        return sorted(
            [d.name for d in inst_mem_dir.iterdir() if d.is_dir() and d.name != "chroma"]
        )



_managers: dict[str, MemoryManager] = {}


def get_memory_manager(bot_qq: str) -> MemoryManager:
    if bot_qq not in _managers:
        _managers[bot_qq] = MemoryManager(bot_qq)
    return _managers[bot_qq]
