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
    # 对外公开基址。留空则 API 层用当前请求的 Host 推导；仅在反代未透传正确 Host 时才需要设置。
    PUBLIC_BASE_URL: str = ""

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

    # TikTok fallback API region list (comma-separated, ordered by priority)
    # When the primary API (fetch_one_video) fails, these regions will be tried
    # sequentially with the fetch_one_video_v3 endpoint
    TIKTOK_FALLBACK_REGIONS: str = "SA,US,JP"

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

    # Usage log
    USAGE_LOG_RETENTION_DAYS: int = 30

    # HTTP client
    HTTP_TIMEOUT_SECONDS: int = 30

    # WeChat Channels streaming proxy
    MAX_CONCURRENT_STREAMS: int = 4
    STREAM_RESUME_MAX_RETRIES: int = 3
    STREAM_CHUNK_SIZE: int = 65536

    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE_PATH: str = "./logs"
    LOG_RETENTION_DAYS: int = 30

    # Provider priority per platform (optional override)
    # Format: "provider1,provider2" e.g. "cobalt" or "tikhub,cobalt"
    # Supported providers: tikhub, cobalt
    PROVIDER_PRIORITY_TIKTOK: str = ""
    PROVIDER_PRIORITY_INSTAGRAM: str = ""
    PROVIDER_PRIORITY_XIAOHONGSHU: str = ""
    PROVIDER_PRIORITY_YOUTUBE: str = ""
    PROVIDER_PRIORITY_PINTEREST: str = ""
    PROVIDER_PRIORITY_FACEBOOK: str = ""
    PROVIDER_PRIORITY_DOUYIN: str = ""
    PROVIDER_PRIORITY_KUAISHOU: str = ""
    PROVIDER_PRIORITY_WECHAT_CHANNELS: str = ""

    model_config = {
        "env_file": ".env",
        "case_sensitive": True,
        "extra": "ignore",
    }


# Global settings instance
settings = Settings()
