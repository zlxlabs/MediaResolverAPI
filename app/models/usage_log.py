"""
API usage log database model.

Records each resolve request for statistics and monitoring.
"""

from sqlalchemy import Column, Integer, String, Boolean, DateTime, Index
from sqlalchemy.sql import func

from ..core.database import Base


class UsageLog(Base):
    """API usage log table — one row per resolve request."""

    __tablename__ = "api_usage_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=True)
    video_id = Column(String(100), nullable=True)
    provider = Column(String(20), nullable=True)
    cache_hit = Column(Boolean, default=False)
    success = Column(Boolean, default=True)
    error_msg = Column(String(500), nullable=True)
    duration_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_usage_created_at", "created_at"),
        Index("ix_usage_platform", "platform"),
    )

    def __repr__(self):
        return (
            f"<UsageLog(platform={self.platform}, "
            f"video_id={self.video_id}, "
            f"success={self.success})>"
        )
