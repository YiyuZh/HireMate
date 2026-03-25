from __future__ import annotations

from abc import ABC, abstractmethod
from hashlib import sha256
import json
import math
import os
import re
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from .metadata import ensure_chunk_metadata
from .store import LocalVectorStore


TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9+/.#-]*|[\u4e00-\u9fff]{2,}")
EMBEDDING_API_KEY_MODES = {"direct_input", "env_name"}
OPENAI_DEFAULT_API_BASE = "https://api.openai.com/v1"
EMBEDDING_PROVIDER_DEFAULTS = {
    "mock": {
        "model": "mock-hash-v1",
        "api_base": "",
        "api_key_env_name": "",
    },
    "openai": {
        "model": "text-embedding-3-small",
        "api_base": OPENAI_DEFAULT_API_BASE,
        "api_key_env_name": "OPENAI_API_KEY",
    },
    "openai_compatible": {
        "model": "text-embedding-3-small",
        "api_base": "",
        "api_key_env_name": "OPENAI_API_KEY",
    },
}
REAL_EMBEDDING_PROVIDERS = {"openai", "openai_compatible"}
API_BASE_REQUIRED_PROVIDERS = {"openai_compatible"}


def get_default_embedding_model(provider: str) -> str:
    provider_norm = str(provider or "mock").strip().lower() or "mock"
    defaults = EMBEDDING_PROVIDER_DEFAULTS.get(provider_norm) or EMBEDDING_PROVIDER_DEFAULTS["mock"]
    return str(defaults.get("model") or "mock-hash-v1")


def get_default_embedding_api_base(provider: str) -> str:
    provider_norm = str(provider or "mock").strip().lower() or "mock"
    defaults = EMBEDDING_PROVIDER_DEFAULTS.get(provider_norm) or EMBEDDING_PROVIDER_DEFAULTS["mock"]
    return str(defaults.get("api_base") or "")


def get_default_embedding_api_key_env_name(provider: str) -> str:
    provider_norm = str(provider or "mock").strip().lower() or "mock"
    defaults = EMBEDDING_PROVIDER_DEFAULTS.get(provider_norm) or EMBEDDING_PROVIDER_DEFAULTS["mock"]
    return str(defaults.get("api_key_env_name") or "")


def _resolve_api_key_details(runtime_cfg: dict[str, Any]) -> dict[str, Any]:
    provider = str(runtime_cfg.get("provider") or "mock").strip().lower() or "mock"
    direct_value = str(runtime_cfg.get("api_key_value") or "").strip()
    raw_mode = str(runtime_cfg.get("api_key_mode") or "").strip().lower()
    mode = raw_mode if raw_mode in EMBEDDING_API_KEY_MODES else ("direct_input" if direct_value else "env_name")
    env_name = str(runtime_cfg.get("api_key_env_name") or "").strip() or get_default_embedding_api_key_env_name(provider)
    env_value = os.getenv(env_name, "").strip() if env_name else ""
    key_value = direct_value if mode == "direct_input" else env_value
    return {
        "mode": mode,
        "env_name": env_name,
        "env_value_present": bool(env_value),
        "key_value": key_value,
        "key_value_present": bool(key_value),
    }


def resolve_embedding_runtime_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(config or {})
    provider = str(
        payload.get("provider")
        or os.getenv("HIREMATE_RAG_EMBEDDING_PROVIDER")
        or "mock"
    ).strip().lower() or "mock"
    model = str(
        payload.get("model")
        or os.getenv("HIREMATE_RAG_EMBEDDING_MODEL")
        or get_default_embedding_model(provider)
    ).strip() or get_default_embedding_model(provider)
    api_base = str(
        payload.get("api_base")
        or os.getenv("HIREMATE_RAG_EMBEDDING_API_BASE")
        or get_default_embedding_api_base(provider)
    ).strip()
    api_key_env_name = str(
        payload.get("api_key_env_name")
        or os.getenv("HIREMATE_RAG_EMBEDDING_API_KEY_ENV_NAME")
        or get_default_embedding_api_key_env_name(provider)
    ).strip()
    api_key_value = str(payload.get("api_key_value") or os.getenv("HIREMATE_RAG_EMBEDDING_API_KEY") or "")
    raw_mode = str(
        payload.get("api_key_mode")
        or os.getenv("HIREMATE_RAG_EMBEDDING_API_KEY_MODE")
        or ""
    ).strip().lower()
    api_key_mode = raw_mode if raw_mode in EMBEDDING_API_KEY_MODES else ("direct_input" if api_key_value.strip() else "env_name")

    timeout_value = payload.get("timeout_seconds", os.getenv("HIREMATE_RAG_EMBEDDING_TIMEOUT_SECONDS", 25))
    try:
        timeout_seconds = max(5, int(timeout_value or 25))
    except (TypeError, ValueError):
        timeout_seconds = 25

    dimension_value = payload.get("dimension", os.getenv("HIREMATE_RAG_EMBEDDING_DIMENSION", 64))
    try:
        dimension = max(16, int(dimension_value or 64))
    except (TypeError, ValueError):
        dimension = 64

    return {
        "provider": provider,
        "model": model,
        "api_base": api_base,
        "api_key_mode": api_key_mode,
        "api_key_env_name": api_key_env_name,
        "api_key_value": api_key_value,
        "timeout_seconds": timeout_seconds,
        "dimension": dimension,
    }


def _build_openai_embeddings_url(api_base: str) -> str:
    base = (api_base or "").strip() or OPENAI_DEFAULT_API_BASE
    if base.endswith("/embeddings"):
        return base

    parsed = urlparse.urlparse(base)
    path = parsed.path.rstrip("/")
    if not path:
        return base.rstrip("/") + "/v1/embeddings"
    return base.rstrip("/") + "/embeddings"


class EmbeddingProvider(ABC):
    def __init__(
        self,
        *,
        provider_name: str,
        model_name: str,
        backend_name: str,
        dimension: int = 0,
        api_base: str = "",
        api_key_mode: str = "env_name",
        api_key_env_name: str = "",
    ) -> None:
        self.provider_name = str(provider_name or "mock").strip().lower() or "mock"
        self.model_name = str(model_name or "mock-hash-v1").strip() or "mock-hash-v1"
        self.backend_name = str(backend_name or self.model_name).strip() or self.model_name
        self.dimension = max(0, int(dimension or 0))
        self.api_base = str(api_base or "").strip()
        self.api_key_mode = str(api_key_mode or "env_name").strip().lower() or "env_name"
        self.api_key_env_name = str(api_key_env_name or "").strip()

    @abstractmethod
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_text(self, text: str) -> list[float]:
        embeddings = self.embed_texts([text])
        return embeddings[0] if embeddings else []

    def safe_config(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model_name,
            "api_base": self.api_base,
            "api_key_mode": self.api_key_mode,
            "api_key_env_name": self.api_key_env_name,
            "backend_name": self.backend_name,
            "dimension": self.dimension,
        }


class MockEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        *,
        dimension: int = 64,
        backend_name: str = "mock-hash-v1",
        provider_name: str = "mock",
        model_name: str = "mock-hash-v1",
    ) -> None:
        resolved_dimension = max(16, int(dimension or 64))
        super().__init__(
            provider_name=provider_name,
            model_name=model_name,
            backend_name=backend_name,
            dimension=resolved_dimension,
            api_base="",
            api_key_mode="env_name",
            api_key_env_name="",
        )

    def _tokenize(self, text: str) -> list[str]:
        return [token.lower() for token in TOKEN_PATTERN.findall(str(text or ""))]

    def embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = self._tokenize(text)
        if not tokens:
            return vector

        for token in tokens:
            digest = sha256(token.encode("utf-8")).digest()
            primary = digest[0] % self.dimension
            secondary = digest[1] % self.dimension
            sign = 1.0 if digest[2] % 2 == 0 else -1.0
            vector[primary] += 1.0
            vector[secondary] += 0.5 * sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm <= 0:
            return vector
        return [value / norm for value in vector]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(text) for text in texts]


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    def __init__(self, runtime_cfg: dict[str, Any]) -> None:
        config = resolve_embedding_runtime_config(runtime_cfg)
        provider = str(config.get("provider") or "openai").strip().lower() or "openai"
        model = str(config.get("model") or get_default_embedding_model(provider)).strip() or get_default_embedding_model(provider)
        api_base = str(config.get("api_base") or get_default_embedding_api_base(provider)).strip()
        if provider in API_BASE_REQUIRED_PROVIDERS and not api_base:
            raise RuntimeError(f"missing api_base for embedding provider {provider}")

        super().__init__(
            provider_name=provider,
            model_name=model,
            backend_name=f"{provider}:{model}",
            dimension=0,
            api_base=api_base or get_default_embedding_api_base(provider),
            api_key_mode=str(config.get("api_key_mode") or "env_name"),
            api_key_env_name=str(config.get("api_key_env_name") or ""),
        )
        self.runtime_cfg = config
        self.timeout_seconds = max(5, int(config.get("timeout_seconds") or 25))

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        clean_texts = [str(text or "").strip() for text in texts]
        if not clean_texts:
            return []

        key_details = _resolve_api_key_details(self.runtime_cfg)
        api_key = str(key_details.get("key_value") or "").strip()
        if not api_key:
            raise RuntimeError("missing api key for rag embedding provider")

        endpoint = _build_openai_embeddings_url(self.api_base)
        body = {
            "model": self.model_name,
            "input": clean_texts,
            "encoding_format": "float",
        }
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urlrequest.urlopen(request, timeout=self.timeout_seconds) as response:
                raw_response = response.read().decode("utf-8")
        except urlerror.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"http {exc.code}: {payload[:240]}") from exc
        except urlerror.URLError as exc:
            raise RuntimeError(f"network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise RuntimeError("timeout") from exc

        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise RuntimeError("embedding response is not valid json") from exc

        data = parsed.get("data")
        if not isinstance(data, list):
            raise RuntimeError("embedding response missing data list")

        indexed_vectors: dict[int, list[float]] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                continue
            indexed_vectors[index] = [float(value) for value in embedding]

        embeddings = [indexed_vectors[index] for index in range(len(clean_texts)) if index in indexed_vectors]
        if len(embeddings) != len(clean_texts):
            raise RuntimeError("embedding response count mismatch")
        if embeddings and len(embeddings[0]) > 0:
            self.dimension = len(embeddings[0])
        return embeddings


def build_embedding_provider(config: dict[str, Any] | None = None) -> EmbeddingProvider:
    runtime_cfg = resolve_embedding_runtime_config(config)
    provider = str(runtime_cfg.get("provider") or "mock").strip().lower() or "mock"
    if provider == "mock":
        return MockEmbeddingProvider(
            dimension=int(runtime_cfg.get("dimension") or 64),
            backend_name="mock-hash-v1",
            model_name=str(runtime_cfg.get("model") or "mock-hash-v1"),
        )
    if provider in REAL_EMBEDDING_PROVIDERS:
        return OpenAICompatibleEmbeddingProvider(runtime_cfg)
    raise RuntimeError(
        f"RAG embedding provider {provider} not implemented. Use openai, openai_compatible, or mock."
    )


def _normalize_document(document: dict[str, Any]) -> dict[str, Any]:
    payload = dict(document or {})
    payload["text"] = str(payload.get("text") or "").strip()
    payload["chunk_id"] = str(payload.get("chunk_id") or "").strip()
    payload["document_id"] = str(payload.get("document_id") or "").strip()
    payload["metadata"] = ensure_chunk_metadata(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {})
    return payload


def index_documents(
    documents: list[dict[str, Any]],
    *,
    store_path: str | None = None,
    reset: bool = False,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_config: dict[str, Any] | None = None,
    collection: str = "default",
) -> dict[str, Any]:
    provider = embedding_provider or build_embedding_provider(embedding_config)
    normalized_docs: list[dict[str, Any]] = []
    texts: list[str] = []

    for raw_document in documents or []:
        document = _normalize_document(raw_document)
        if not document.get("chunk_id") or not document.get("text"):
            continue
        normalized_docs.append(document)
        texts.append(document["text"])

    embeddings: dict[str, list[float]] = {}
    vectors = provider.embed_texts(texts) if texts else []
    if texts and len(vectors) != len(normalized_docs):
        raise RuntimeError("embedding provider returned an unexpected vector count")
    for index, document in enumerate(normalized_docs):
        embeddings[document["chunk_id"]] = vectors[index]

    inferred_dimension = provider.dimension or (len(next(iter(embeddings.values()))) if embeddings else 0)
    store = LocalVectorStore(store_path, collection=collection)
    summary = store.save_documents(
        normalized_docs,
        embeddings,
        embedding_backend=provider.backend_name,
        embedding_dim=inferred_dimension,
        embedding_config=provider.safe_config(),
        reset=reset,
    )
    summary["indexed_documents"] = len(normalized_docs)
    summary["collection"] = collection
    return summary
