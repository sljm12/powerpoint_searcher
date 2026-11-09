from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    token: str
    database_url: str
    database_connect_timeout: int
    database_statement_timeout_ms: int
    query_timeout: float
    embedding_url: str
    embedding_api_key: str
    embedding_model: str
    embedding_expected_model: str
    embedding_revision: str
    embedding_dimensions: int
    embedding_request_dimensions: int | None
    query_prefix: str
    document_prefix: str
    tokenizer_path: Path
    max_input_tokens: int
    chunk_tokens: int
    chunk_overlap: int
    batch_size: int
    max_batch_tokens: int
    embedding_timeout: float
    max_attempts: int
    max_response_bytes: int
    embedding_location: str
    allow_cloud: bool

    @classmethod
    def from_environment(cls) -> Settings:
        def integer(name: str, default: int | None = None) -> int:
            value = os.getenv(name)
            if value is None or value == "":
                if default is None:
                    raise ValueError(f"{name} is required")
                return default
            try:
                parsed = int(value)
            except ValueError:
                raise ValueError(f"{name} must be an integer") from None
            return parsed

        def number(name: str, default: float) -> float:
            try:
                parsed = float(os.getenv(name, str(default)))
            except ValueError:
                raise ValueError(f"{name} must be a number") from None
            if parsed <= 0:
                raise ValueError(f"{name} must be positive")
            return parsed

        location = os.getenv("SEARCH_EMBEDDING_LOCATION", "local")
        allow_cloud = os.getenv("SEARCH_EMBEDDING_ALLOW_CLOUD", "false").lower() == "true"
        raw_dimensions = integer("SEARCH_EMBEDDING_EXPECTED_DIMENSIONS", 0)
        request_dimensions = os.getenv("SEARCH_EMBEDDING_REQUEST_DIMENSIONS", "").strip()
        settings = cls(
            host=os.getenv("SEARCH_HOST", "127.0.0.1"),
            port=integer("SEARCH_PORT", 8002),
            token=os.getenv("SEARCH_TOKEN", "").strip(),
            database_url=os.getenv("SEARCH_DATABASE_URL", "").strip(),
            database_connect_timeout=integer("SEARCH_DATABASE_CONNECT_TIMEOUT_SECONDS", 5),
            database_statement_timeout_ms=integer("SEARCH_DATABASE_STATEMENT_TIMEOUT_SECONDS", 5) * 1000,
            query_timeout=number("SEARCH_QUERY_TIMEOUT_SECONDS", 10),
            embedding_url=os.getenv("SEARCH_EMBEDDING_BASE_URL", "").strip(),
            embedding_api_key=os.getenv("SEARCH_EMBEDDING_API_KEY", ""),
            embedding_model=os.getenv("SEARCH_EMBEDDING_MODEL", "").strip(),
            embedding_expected_model=os.getenv("SEARCH_EMBEDDING_EXPECTED_MODEL", "").strip(),
            embedding_revision=os.getenv("SEARCH_EMBEDDING_REVISION", "").strip(),
            embedding_dimensions=raw_dimensions,
            embedding_request_dimensions=int(request_dimensions) if request_dimensions else None,
            query_prefix=os.getenv("SEARCH_EMBEDDING_QUERY_PREFIX", ""),
            document_prefix=os.getenv("SEARCH_EMBEDDING_DOCUMENT_PREFIX", ""),
            tokenizer_path=Path(os.getenv("SEARCH_EMBEDDING_TOKENIZER_PATH", "")),
            max_input_tokens=integer("SEARCH_EMBEDDING_MAX_INPUT_TOKENS", 0),
            chunk_tokens=integer("SEARCH_EMBEDDING_CHUNK_TOKENS", 256),
            chunk_overlap=integer("SEARCH_EMBEDDING_CHUNK_OVERLAP", 32),
            batch_size=integer("SEARCH_EMBEDDING_BATCH_SIZE", 1),
            max_batch_tokens=integer("SEARCH_EMBEDDING_MAX_BATCH_TOKENS", 256),
            embedding_timeout=number("SEARCH_EMBEDDING_TIMEOUT_SECONDS", 30),
            max_attempts=integer("SEARCH_EMBEDDING_MAX_ATTEMPTS", 3),
            max_response_bytes=integer("SEARCH_EMBEDDING_MAX_RESPONSE_BYTES", 8 * 1024 * 1024),
            embedding_location=location,
            allow_cloud=allow_cloud,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.host not in ("127.0.0.1", "::1", "localhost") or not 1 <= self.port <= 65535:
            raise ValueError("Search must bind to a loopback address and valid port")
        limits = (self.database_connect_timeout, self.database_statement_timeout_ms, self.batch_size,
                  self.max_batch_tokens, self.max_attempts, self.max_response_bytes)
        if any(value <= 0 for value in limits) or self.chunk_overlap < 0:
            raise ValueError("Search limits must be positive")
        if self.embedding_location not in ("local", "cloud"):
            raise ValueError("Embedding location must be local or cloud")
        if not all((self.embedding_url, self.embedding_model, self.embedding_revision,
                    self.embedding_dimensions, self.max_input_tokens,
                    str(self.tokenizer_path))):
            return
        if self.embedding_dimensions <= 0 or self.max_input_tokens <= 0 or self.chunk_tokens <= 0:
            raise ValueError("Embedding dimensions and token limits must be positive")
        if self.chunk_overlap >= self.chunk_tokens or self.chunk_tokens > self.max_input_tokens:
            raise ValueError("Chunk token and overlap settings are inconsistent")
        if self.embedding_request_dimensions is not None and self.embedding_request_dimensions <= 0:
            raise ValueError("Requested embedding dimensions must be positive")
        parsed = urlsplit(self.embedding_url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Embedding URL cannot contain credentials, a query, or a fragment")
        if not parsed.hostname or parsed.path.endswith("/embeddings"):
            raise ValueError("Embedding URL must be an API base URL")
        if self.embedding_location == "local":
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = parsed.hostname == "localhost"
            if parsed.scheme != "http" or not loopback:
                raise ValueError("Local embedding endpoints must use loopback HTTP")
        elif parsed.scheme != "https" or not self.allow_cloud:
            raise ValueError("Cloud embeddings require HTTPS and explicit cloud approval")
