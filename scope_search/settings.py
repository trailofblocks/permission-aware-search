"""Environment settings for the standalone service."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql://scope:search-local@127.0.0.1:55493/scope"
    demo_enabled: bool = False
    ollama_url: str | None = None
    ollama_model: str = "qwen2.5:1.5b"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            demo_enabled=os.getenv("SCOPE_DEMO", "0") == "1",
            ollama_url=os.getenv("SCOPE_OLLAMA_URL") or None,
            ollama_model=os.getenv("SCOPE_OLLAMA_MODEL", cls.ollama_model),
        )
