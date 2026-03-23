"""
缓存服务模块
管理视频信息的数据库缓存
"""

from typing import Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from loguru import logger

from ..models.video_cache import VideoCache
from .platforms.base import VideoInfo
from ..core.config import settings


class CacheService:
    """
    缓存服务类
    提供视频信息的缓存功能
    """

    def __init__(self, db: Session):
        """
        初始化缓存服务

        Args:
            db: 数据库会话
        """
        self.db = db

    def get_cached_video(self, platform: str, video_id: str) -> tuple[Optional[VideoInfo], Optional[str]]:
        """
        获取缓存的视频信息和翻译结果

        Args:
            platform: 平台名称
            video_id: 视频ID

        Returns:
            tuple[Optional[VideoInfo], Optional[str]]: 缓存的视频信息和翻译后的描述，如果不存在或已过期返回None
        """
        if not settings.CACHE_ENABLED:
            return None

        try:
            cache_record = self.db.query(VideoCache).filter(
                VideoCache.platform == platform,
                VideoCache.video_id == video_id
            ).first()

            if not cache_record:
                logger.debug(f"缓存中未找到视频: {platform}:{video_id}")
                return None, None

            if cache_record.is_expired:
                logger.info(f"缓存已过期，删除记录: {platform}:{video_id}")
                self.db.delete(cache_record)
                self.db.commit()
                return None, None

            # 从缓存数据构建VideoInfo对象
            cached_data = cache_record.video_data
            video_info = VideoInfo(
                video_id=cached_data.get("video_id", ""),
                platform=cached_data.get("platform", ""),
                title=cached_data.get("title", ""),
                description=cached_data.get("description", ""),
                author_name=cached_data.get("author_name", ""),
                author_id=cached_data.get("author_id", ""),
                video_url=cached_data.get("video_url", ""),
                width=cached_data.get("width", 0),
                height=cached_data.get("height", 0),
                duration=cached_data.get("duration"),
                quality=cached_data.get("quality"),
                view_count=cached_data.get("view_count"),
                like_count=cached_data.get("like_count"),
                comment_count=cached_data.get("comment_count"),
                share_count=cached_data.get("share_count"),
                collect_count=cached_data.get("collect_count"),
                create_time=datetime.fromisoformat(cached_data["create_time"]) if cached_data.get("create_time") else None,
                publish_time=cached_data.get("publish_time"),
                provider=cached_data.get("provider", cache_record.provider),  # 使用缓存记录的 provider
                raw_data=cached_data.get("raw_data")
            )

            logger.info(f"从缓存获取视频信息: {platform}:{video_id}")
            return video_info, cache_record.translated_desc

        except Exception as e:
            logger.error(f"获取缓存失败: {e}")
            return None, None

    def cache_video(self, platform: str, video_id: str, video_info: VideoInfo, translated_desc: Optional[str] = None) -> bool:
        """
        缓存视频信息和翻译结果

        Args:
            platform: 平台名称
            video_id: 视频ID
            video_info: 视频信息对象
            translated_desc: 翻译后的描述（可选）

        Returns:
            bool: 是否缓存成功
        """
        if not settings.CACHE_ENABLED:
            return False

        try:
            # 检查是否已存在缓存记录
            existing_cache = self.db.query(VideoCache).filter(
                VideoCache.platform == platform,
                VideoCache.video_id == video_id
            ).first()

            # 准备缓存数据
            cache_data = video_info.to_dict()

            if existing_cache:
                # 更新现有缓存
                existing_cache.video_data = cache_data
                existing_cache.translated_desc = translated_desc
                existing_cache.provider = video_info.provider or "tikhub"
                existing_cache.cached_at = datetime.now(timezone.utc)
                existing_cache.expires_at = datetime.now(timezone.utc) + \
                    timedelta(hours=settings.CACHE_TTL_HOURS)
                logger.info(f"更新缓存: {platform}:{video_id}")
            else:
                # 创建新缓存记录
                new_cache = VideoCache(
                    platform=platform,
                    video_id=video_id,
                    video_data=cache_data,
                    translated_desc=translated_desc,
                    provider=video_info.provider or "tikhub"
                )
                self.db.add(new_cache)
                logger.info(f"创建新缓存: {platform}:{video_id}")

            self.db.commit()
            return True

        except Exception as e:
            logger.error(f"缓存视频信息失败: {e}")
            self.db.rollback()
            return False

    def clear_cache(self, platform: str, video_id: str) -> bool:
        """
        清除特定的缓存记录

        Args:
            platform: 平台名称
            video_id: 视频ID

        Returns:
            bool: 是否清除成功
        """
        try:
            cache_record = self.db.query(VideoCache).filter(
                VideoCache.platform == platform,
                VideoCache.video_id == video_id
            ).first()

            if cache_record:
                self.db.delete(cache_record)
                self.db.commit()
                logger.info(f"清除缓存: {platform}:{video_id}")
                return True
            else:
                logger.debug(f"缓存记录不存在: {platform}:{video_id}")
                return False

        except Exception as e:
            logger.error(f"清除缓存失败: {e}")
            self.db.rollback()
            return False

    def clear_expired_cache(self) -> int:
        """
        清理所有过期的缓存记录

        Returns:
            int: 清理的记录数量
        """
        try:
            current_time = datetime.now(timezone.utc)
            count = self.db.query(VideoCache).filter(
                VideoCache.expires_at < current_time
            ).delete()

            if count > 0:
                self.db.commit()
                logger.info(f"清理了{count}条过期缓存记录")

            return count

        except Exception as e:
            logger.error(f"清理过期缓存失败: {e}")
            self.db.rollback()
            return 0