"""
Application configuration module.

Loads settings from environment variables using Pydantic.
"""

import os
from pathlib import Path
from typing import List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration."""

    # Application settings
    APP_NAME: str = "MediaResolverAPI"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Database
    DATABASE_URL: str = "sqlite:///./data/media_resolver.db"

    def __init__(self, **kwargs):
        """Initialize config, resolve relative database path."""
        super().__init__(**kwargs)

        if self.DATABASE_URL.startswith("sqlite:///./"):
            current_file = Path(__file__)
            project_root = current_file

            while project_root.parent != project_root:
                if (project_root / "pyproject.toml").exists():
                    break
                project_root = project_root.parent

            if not (project_root / "pyproject.toml").exists():
                project_root = Path.cwd()

            db_relative_path = self.DATABASE_URL.replace("sqlite:///./", "")
            db_absolute_path = project_root / db_relative_path
            db_absolute_path.parent.mkdir(parents=True, exist_ok=True)
            self.DATABASE_URL = f"sqlite:///{db_absolute_path}"

    # API Authentication
    API_KEY: str = ""

    # TikHub API
    TIKHUB_API_BASE: str = "https://api.tikhub.io"
    TIKHUB_API_KEY: str = ""
    TIKHUB_RATE_LIMIT: int = 10

    # Cobalt API
    COBALT_API_BASE: str = ""
    COBALT_ENABLED: bool = True
    COBALT_TIMEOUT_SECONDS: int = 60

    # OpenAI (translation)
    OPENAI_API_KEY: str = ""
    OPENAI_API_BASE: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-4o-mini"
    TRANSLATION_ENABLED: bool = True

    # Cache
    CACHE_ENABLED: bool = True
    CACHE_TTL_HOURS: int = 12

    # HTTP client
    HTTP_TIMEOUT_SECONDS: int = 30

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE_PATH: str = "./logs"
    LOG_RETENTION_DAYS: int = 30

    # Provider priority (optional override)
    PROVIDER_PRIORITY: dict = {}

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore",
    }


# Global settings instance
settings = Settings()
