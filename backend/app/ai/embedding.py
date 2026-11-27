"""向量模型（embedding）：OpenAI 兼容的 /v1/embeddings 接口，配置见 EMBEDDING_*。

服务只在内网时经本机 SSH 隧道访问；隧道断了相似问答检索就查不了，回复建议照常生成。
"""

import httpx

from app.config import settings


class EmbeddingError(Exception):
    pass


class EmbeddingClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", dim: int = 1024, timeout: float = 10,
                 client: httpx.Client | None = None):
        self.url = base_url.rstrip("/") + "/embeddings"
        self.model, self.api_key, self.dim = model, api_key, dim
        # 内网地址经本机隧道访问，不走系统代理
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)

    def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = self._client.post(self.url, json={"model": self.model, "input": texts}, headers=headers)
            r.raise_for_status()
            rows = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            vectors = [[float(x) for x in d["embedding"]] for d in rows]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"向量模型调用失败：{exc}") from exc
        if len(vectors) != len(texts) or any(len(v) != self.dim for v in vectors):
            raise EmbeddingError(f"向量模型返回的条数或维度不对（应为 {len(texts)} 条 {self.dim} 维）")
        return vectors


_client: EmbeddingClient | None = None


def get_client() -> EmbeddingClient | None:
    """没配置 embedding 时返回 None（不查相似问答）。"""
    global _client
    if _client is not None:
        return _client
    if not (settings.embedding_base_url and settings.embedding_model):
        return None
    _client = EmbeddingClient(settings.embedding_base_url, settings.embedding_model, settings.embedding_api_key,
                              settings.embedding_dim, settings.embedding_timeout)
    return _client


def set_client(client: EmbeddingClient | None) -> None:
    global _client
    _client = client
