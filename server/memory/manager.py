"""
记忆管理器 — 为每个 QQ 用户建立独立的记忆文件夹，管理 MD 文件的 CRUD。
同时与 ChromaDB 向量存储同步。
"""

import re
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from server.config import get_memory_dir, get_chroma_dir
from server.memory.vector_store import VectorStore


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

    def remember(self, user_id: str, category: str, title: str, content: str):
        """写入一条记忆，同步向量索引。"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry_id = self._make_entry_id(user_id, category, title)
        full_text = f"# {title}\n\n> 类型: {category}\n> 时间: {now}\n> ID: {entry_id}\n\n{content}"

        filepath = self._entry_file(user_id, category, title)
        filepath.write_text(full_text, encoding="utf-8")

        try:
            vs = self._get_vs(user_id)
            vs.add(entry_id, full_text, {"category": category, "title": title, "date": now})
        except Exception:
            pass

    # ---- 读取 ----

    def recall_by_vector(self, user_id: str, query: str, n: int = 10) -> list[dict]:
        """向量检索记忆。"""
        try:
            vs = self._get_vs(user_id)
            results = vs.search(query, n)
            return [
                {"id": r["id"], "text": r["text"], "score": r["score"], "meta": r["meta"]}
                for r in results
            ]
        except Exception:
            return []

    def recall_by_category(self, user_id: str, category: str) -> list[dict]:
        """按分类列出记忆。"""
        user_dir = self._get_user_dir(user_id)
        memories = []
        for f in sorted(user_dir.glob(f"{category}_*.md")):
            text = f.read_text(encoding="utf-8")
            memories.append({"file": str(f), "text": text})
        return memories

    def recall_all(self, user_id: str) -> list[dict]:
        """列出该用户所有记忆。"""
        user_dir = self._get_user_dir(user_id)
        memories = []
        for f in sorted(user_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            memories.append({"file": f.name, "text": text})
        return memories

    def recall_by_date(self, user_id: str, date_str: str) -> list[dict]:
        """按日期查找记忆。date_str 格式 YYYY-MM-DD"""
        user_dir = self._get_user_dir(user_id)
        memories = []
        for f in sorted(user_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            if date_str in text:
                memories.append({"file": f.name, "text": text})
        return memories

    def recall_recent(self, user_id: str, days: int = 7) -> list[dict]:
        """获取最近 N 天的记忆。"""
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        user_dir = self._get_user_dir(user_id)
        memories = []
        for f in sorted(user_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            m = re.search(r"时间: (\d{4}-\d{2}-\d{2})", text)
            if m and m.group(1) >= cutoff:
                memories.append({"file": f.name, "text": text})
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

    def extract_keywords(self, text: str) -> list[str]:
        """从文本中提取可能的关键词用于记忆检索。"""
        keywords = []
        patterns = [
            r"(?:我叫|我是|我的名字是|叫我)(\S{1,8})",
            r"(?:我喜欢|我爱)(\S{1,20})",
            r"(?:我是做|我从事|我的工作是)(\S{1,30})",
            r"(?:我的生日是|生日在)(\S{1,20})",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                keywords.append(m.group(0))
        return keywords or [text[:100]]

    def auto_remember_from_message(self, user_id: str, user_name: str, message: str, reply: str):
        """从一轮对话中自动判断并存储值得记住的信息。"""
        keywords = self.extract_keywords(message)
        if keywords:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            content = f"用户 {user_name}({user_id}) 说: {message}\n咱回应: {reply}"
            title = keywords[0].replace("/", "-")[:40]
            self.remember(user_id, "对话记忆", title, content)


_managers: dict[str, MemoryManager] = {}


def get_memory_manager(bot_qq: str) -> MemoryManager:
    if bot_qq not in _managers:
        _managers[bot_qq] = MemoryManager(bot_qq)
    return _managers[bot_qq]
