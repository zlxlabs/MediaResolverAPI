"""
视频缓存数据库模型
"""

from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, DateTime, JSON, Index, Text
from sqlalchemy.sql import func

from ..core.database import Base
from ..core.config import settings


class VideoCache(Base):
    """
    视频缓存表模型
    缓存从各个提供者（TikHub、Cobalt等）获取的视频信息，减少API调用费用
    """
    __tablename__ = "video_cache"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    platform = Column(String(20), nullable=False)
    video_id = Column(String(100), nullable=False)
    video_data = Column(JSON, nullable=False)
    translated_desc = Column(Text, nullable=True)  # 添加翻译后的描述字段
    provider = Column(String(20), nullable=False, default="tikhub", index=True)  # 数据提供者：tikhub, cobalt
    cached_at = Column(DateTime, nullable=False, default=func.now())
    expires_at = Column(DateTime, nullable=False)

    # 创建联合唯一索引
    __table_args__ = (
        Index('ix_platform_video_id', 'platform', 'video_id', unique=True),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.expires_at:
            # 设置过期时间为当前时间 + 缓存TTL小时数
            self.expires_at = datetime.utcnow() + timedelta(hours=settings.CACHE_TTL_HOURS)

    @property
    def is_expired(self) -> bool:
        """检查缓存是否已过期"""
        return datetime.utcnow() > self.expires_at

    def __repr__(self):
        return f"<VideoCache(platform={self.platform}, video_id={self.video_id}, expired={self.is_expired})>"