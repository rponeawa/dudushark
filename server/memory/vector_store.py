"""
ChromaDB 向量存储封装 — 使用 SiliconFlow BAAI/bge-m3 嵌入模型。
"""

import logging
import os
import re

os.environ.setdefault("CHROMADB_TELEMETRY", "False")

from pathlib import Path

import httpx
from chromadb import PersistentClient

logger = logging.getLogger("dudushark.vector")

EMBEDDING_API_URL = "https://api.siliconflow.cn/v1/embeddings"
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")

_EMBED_FAIL_COUNT = 0


class SiliconFlowEmbedding:
    """通过 SiliconFlow API 获取嵌入向量。"""

    def __init__(self, api_key: str = EMBEDDING_API_KEY, model: str = EMBEDDING_MODEL):
        self.api_key = api_key
        self.model = model

    def name(self) -> str:
        return f"siliconflow-{self.model}"

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self._embed(input)

    def embed_query(self, input: list[str]) -> list[list[float]]:
        return self._embed(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        return self._embed(input)

    def _embed(self, input: list[str]) -> list[list[float]]:
        global _EMBED_FAIL_COUNT
        if not input:
            return []
        try:
            # 在线程池中执行同步 HTTP 请求，避免阻塞 asyncio 事件循环
            import concurrent.futures
            def _do_request():
                return httpx.post(
                    EMBEDDING_API_URL,
                    json={"model": self.model, "input": input, "encoding_format": "float"},
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=30,
                )
            resp = concurrent.futures.ThreadPoolExecutor(max_workers=1).submit(_do_request).result(timeout=35)
            if resp.status_code != 200:
                _EMBED_FAIL_COUNT += 1
                if _EMBED_FAIL_COUNT <= 3 or _EMBED_FAIL_COUNT % 20 == 0:
                    logger.warning(f"嵌入 API 返回 {resp.status_code}: {resp.text[:200]}")
                dim = 1024
                return [[0.0] * dim for _ in input]
            _EMBED_FAIL_COUNT = 0
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        except Exception as e:
            _EMBED_FAIL_COUNT += 1
            if _EMBED_FAIL_COUNT <= 3 or _EMBED_FAIL_COUNT % 20 == 0:
                logger.warning(f"嵌入 API 调用失败: {e}")
            dim = 1024
            return [[0.0] * dim for _ in input]


_clients: dict[str, PersistentClient] = {}


def _get_client(chroma_dir: Path) -> PersistentClient:
    key = str(chroma_dir.resolve())
    if key not in _clients:
        chroma_dir.mkdir(parents=True, exist_ok=True)
        _clients[key] = PersistentClient(path=key)
    return _clients[key]


class VectorStore:
    """每个用户一个 ChromaDB collection (共享 PersistentClient)。"""

    def __init__(self, chroma_dir: Path, user_id: str):
        self.client = _get_client(chroma_dir)
        # ChromaDB name: 3-512 chars, [a-zA-Z0-9._-], must start/end with alphanumeric
        safe = re.sub(r"[^a-zA-Z0-9._-]", "_", user_id)
        safe = safe.strip("_")
        if len(safe) < 3:
            safe = safe + "usr"
        safe_name = f"mem_{safe}"
        self.ef = SiliconFlowEmbedding()
        try:
            self.collection = self.client.get_collection(
                name=safe_name, embedding_function=self.ef
            )
        except Exception:
            self.collection = self.client.create_collection(
                name=safe_name, embedding_function=self.ef
            )

    def add(self, doc_id: str, text: str, metadata: dict):
        self.collection.upsert(
            ids=[doc_id],
            documents=[text],
            metadatas=[metadata],
        )

    def search(self, query: str, n: int = 10) -> list[dict]:
        results = self.collection.query(query_texts=[query], n_results=n)
        out = []
        if results["ids"] and results["ids"][0]:
            for i, rid in enumerate(results["ids"][0]):
                out.append({
                    "id": rid,
                    "text": results["documents"][0][i] if results["documents"] else "",
                    "score": results["distances"][0][i] if results["distances"] else 0,
                    "meta": results["metadatas"][0][i] if results["metadatas"] else {},
                })
        return out

    def delete(self, doc_id: str):
        self.collection.delete(ids=[doc_id])

    def clear(self):
        self.client.delete_collection(self.collection.name)
