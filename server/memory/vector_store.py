"""
ChromaDB 向量存储封装 — 使用 SiliconFlow BAAI/bge-m3 嵌入模型。
"""

import logging
import os

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

    def __call__(self, texts: list[str]) -> list[list[float]]:
        global _EMBED_FAIL_COUNT
        if not texts:
            return []
        try:
            resp = httpx.post(
                EMBEDDING_API_URL,
                json={"model": self.model, "input": texts, "encoding_format": "float"},
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code != 200:
                _EMBED_FAIL_COUNT += 1
                if _EMBED_FAIL_COUNT <= 3 or _EMBED_FAIL_COUNT % 20 == 0:
                    logger.warning(f"嵌入 API 返回 {resp.status_code}: {resp.text[:200]}")
                # 返回零向量而非随机向量，确保不产生虚假相似度
                dim = 1024
                return [[0.0] * dim for _ in texts]
            _EMBED_FAIL_COUNT = 0
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        except Exception as e:
            _EMBED_FAIL_COUNT += 1
            if _EMBED_FAIL_COUNT <= 3 or _EMBED_FAIL_COUNT % 20 == 0:
                logger.warning(f"嵌入 API 调用失败: {e}")
            dim = 1024
            return [[0.0] * dim for _ in texts]


class VectorStore:
    """每个用户一个 ChromaDB collection。"""

    def __init__(self, chroma_dir: Path, user_id: str):
        chroma_dir.mkdir(parents=True, exist_ok=True)
        self.client = PersistentClient(path=str(chroma_dir))
        safe_name = f"mem_{user_id}".replace("-", "_").replace(".", "_")
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
