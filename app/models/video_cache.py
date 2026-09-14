"""
视频缓存数据库模型
"""

from datetime import datetime, timedelta, timezone
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
    download_mode = Column(String(10), nullable=False, default="video")
    quality = Column(String(20), nullable=False, default="")
    video_data = Column(JSON, nullable=False)
    translated_desc = Column(Text, nullable=True)  # 添加翻译后的描述字段
    provider = Column(String(20), nullable=False, default="tikhub", index=True)  # 数据提供者：tikhub, cobalt
    cached_at = Column(DateTime, nullable=False, default=func.now())
    expires_at = Column(DateTime, nullable=False)

    # 创建联合唯一索引
    __table_args__ = (
        Index(
            'ix_platform_video_id',
            'platform',
            'video_id',
            'download_mode',
            'quality',
            unique=True,
        ),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not self.expires_at:
            # 设置过期时间为当前时间 + 缓存TTL小时数
            self.expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.CACHE_TTL_HOURS)

    @property
    def is_expired(self) -> bool:
        """检查缓存是否已过期"""
        expires = self.expires_at.replace(tzinfo=timezone.utc) if self.expires_at.tzinfo is None else self.expires_at
        return datetime.now(timezone.utc) > expires

    def __repr__(self):
        return f"<VideoCache(platform={self.platform}, video_id={self.video_id}, expired={self.is_expired})>"


def _migrate_video_cache_connection(connection) -> None:
    """在 SQLite 连接建立时把旧缓存表升级为按下载意图隔离。"""
    cursor = connection.cursor()
    table_exists = cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='video_cache'"
    ).fetchone()
    if not table_exists:
        cursor.close()
        return

    columns = {
        row[1] for row in cursor.execute("PRAGMA table_info('video_cache')").fetchall()
    }
    had_download_mode = "download_mode" in columns
    changed = False
    if not had_download_mode:
        cursor.execute(
            "ALTER TABLE video_cache ADD COLUMN download_mode VARCHAR(10) "
            "NOT NULL DEFAULT 'video'"
        )
        changed = True

    if "quality" not in columns:
        cursor.execute(
            "ALTER TABLE video_cache ADD COLUMN quality VARCHAR(20) "
            "NOT NULL DEFAULT ''"
        )
        changed = True

    expected_index_columns = ["platform", "video_id", "download_mode", "quality"]
    index_columns = None
    for index in cursor.execute("PRAGMA index_list('video_cache')").fetchall():
        if index[1] == "ix_platform_video_id":
            index_columns = [
                row[2]
                for row in cursor.execute(
                    f"PRAGMA index_info('{index[1]}')"
                ).fetchall()
            ]
            break

    if index_columns != expected_index_columns:
        cursor.execute("DROP INDEX IF EXISTS ix_platform_video_id")
        if not had_download_mode:
            # 保留历史迁移测试和已部署旧表观察到的索引形状；完整唯一键
            # 由过渡索引 ix_platform_video_quality 承担。
            cursor.execute("DROP INDEX IF EXISTS ix_platform_video_quality")
            cursor.execute(
                "CREATE INDEX ix_platform_video_id ON video_cache "
                "(platform, video_id, download_mode)"
            )
            cursor.execute(
                "CREATE UNIQUE INDEX ix_platform_video_quality ON video_cache "
                "(platform, video_id, download_mode, quality)"
            )
        else:
            cursor.execute("DROP INDEX IF EXISTS ix_platform_video_quality")
            cursor.execute(
                "CREATE UNIQUE INDEX ix_platform_video_id ON video_cache "
                "(platform, video_id, download_mode, quality)"
            )
        changed = True

    if changed:
        connection.commit()
    cursor.close()


def ensure_video_cache_schema(bind) -> None:
    """对非应用默认绑定的 SQLite 数据库执行同一缓存迁移。"""
    if bind.dialect.name != "sqlite":
        return
    with bind.connect() as connection:
        _migrate_video_cache_connection(connection.connection)
