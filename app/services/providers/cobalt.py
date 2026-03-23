"""
Cobalt 视频下载服务提供者

Cobalt 是一个通用的视频下载服务，支持多个平台
作为 TikHub 的备用方案
"""

from typing import Dict
from loguru import logger

from .base import BaseProvider, ProviderError, VideoNotFoundError
from ...core.config import settings


class CobaltProvider(BaseProvider):
    """
    Cobalt API 提供者

    支持平台：
    - tiktok
    - instagram
    - youtube
    - xiaohongshu
    - pinterest
    - facebook

    注意：Cobalt 返回的信息较少，主要用于获取视频下载链接
    """

    # Cobalt 支持的平台列表
    SUPPORTED_PLATFORMS = [
        "tiktok",
        "instagram",
        "youtube",
        "xiaohongshu",
        "pinterest",
        "facebook",
    ]

    def __init__(self):
        """初始化 Cobalt 提供者"""
        super().__init__()
        self.api_base = getattr(settings, "COBALT_API_BASE", "")

        if not self.api_base:
            self.log_warning("Cobalt API base URL is not configured")

    @property
    def provider_name(self) -> str:
        """提供者名称"""
        return "cobalt"

    def supports_platform(self, platform: str) -> bool:
        """
        检查是否支持指定平台

        Args:
            platform: 平台名称

        Returns:
            bool: 是否支持
        """
        return platform.lower() in self.SUPPORTED_PLATFORMS

    async def fetch_video_info(
        self,
        platform: str,
        video_id: str,
        original_url: str,
        **kwargs
    ) -> Dict:
        """
        从 Cobalt API 获取视频下载信息

        Args:
            platform: 平台名称
            video_id: 视频ID
            original_url: 原始视频URL (Cobalt 需要完整的 URL)
            **kwargs: 其他参数

        Returns:
            Dict: Cobalt API 返回的数据，包含：
                - status: "redirect" | "tunnel" | "picker" | "error"
                - url: 视频下载链接
                - filename: 文件名

        Raises:
            VideoNotFoundError: 视频不存在或无法解析
            ProviderError: API 调用失败
        """
        platform = platform.lower()

        if not self.supports_platform(platform):
            raise ProviderError(f"Platform '{platform}' is not supported by Cobalt provider")

        if not self.api_base:
            raise ProviderError("Cobalt API base URL is not configured")

        self.log_info(
            f"Fetching video info from Cobalt",
            platform=platform,
            video_id=video_id,
            url=original_url
        )

        try:
            # Use standard httpx client to avoid conflicts with HTTPClient
            import httpx

            self.log_info(f"Sending request to Cobalt API: {self.api_base}")

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.api_base,
                    json={"url": original_url},
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json"
                    }
                )

                response_text_preview = response.text[:500] if response.text else "Empty"
                self.log_info(
                    f"Cobalt API response: status={response.status_code}, "
                    f"content_type={response.headers.get('content-type', 'unknown')}, "
                    f"length={len(response.text) if response.text else 0}"
                )

                if response.status_code != 200:
                    self.log_error(
                        f"Cobalt API returned non-200: status={response.status_code}, "
                        f"body={response_text_preview}"
                    )
                    raise ProviderError(
                        f"Cobalt API error {response.status_code}: {response_text_preview[:200]}"
                    )

                # Parse JSON response
                try:
                    data = response.json()
                except Exception as json_err:
                    self.log_error(
                        f"Failed to parse Cobalt JSON response: {json_err}",
                        response_text=response.text[:500] if response.text else "Empty"
                    )
                    raise ProviderError(f"Invalid JSON response from Cobalt: {response.text[:200]}")

                # Log raw response for debugging
                self.log_info(
                    f"Cobalt raw response received",
                    response_type=type(data).__name__,
                    response_keys=list(data.keys()) if isinstance(data, dict) else None,
                    response_preview=str(data)[:300]
                )

                # Validate response is a dictionary
                if not isinstance(data, dict):
                    raise ProviderError(
                        f"Unexpected Cobalt response type: {type(data).__name__}, "
                        f"expected dict. Response: {str(data)[:200]}"
                    )

                # Check response status
                status = data.get("status")

                if status == "error":
                    # Handle error response - support both old and new format
                    error_info = data.get("error", {})
                    if isinstance(error_info, dict):
                        error_code = error_info.get("code", "unknown")
                    else:
                        error_code = str(error_info)
                    raise VideoNotFoundError(f"Cobalt error: {error_code}")

                # Support all Cobalt status types:
                # - tunnel/redirect: direct download
                # - stream: streaming (legacy)
                # - local-processing: local processing (Cobalt v10+)
                # - picker: multiple options
                valid_statuses = ["redirect", "tunnel", "picker", "stream", "local-processing"]
                if status not in valid_statuses:
                    raise ProviderError(
                        f"Unexpected Cobalt response status: '{status}'. "
                        f"Valid statuses: {valid_statuses}. "
                        f"Full response: {str(data)[:300]}"
                    )

                # Validate required fields based on status type
                if status in ["redirect", "tunnel", "stream"]:
                    if "url" not in data:
                        raise ProviderError(
                            f"Cobalt response missing 'url' field for status '{status}'. "
                            f"Response: {str(data)[:300]}"
                        )
                elif status == "local-processing":
                    output = data.get("output", {})
                    if "url" not in data and "tunnel" not in output:
                        self.log_warning(
                            f"local-processing response structure",
                            data_keys=list(data.keys()),
                            output_keys=list(output.keys()) if isinstance(output, dict) else None
                        )

                self.log_info(
                    f"Successfully fetched video info from Cobalt",
                    platform=platform,
                    video_id=video_id,
                    status=status
                )

                # 添加额外信息，方便后续处理
                data["_platform"] = platform
                data["_video_id"] = video_id
                data["_original_url"] = original_url

                return data

        except (VideoNotFoundError, ProviderError):
            raise
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            self.log_error(
                f"Failed to fetch video info from Cobalt: {type(e).__name__}: {e}\n"
                f"Traceback:\n{tb_str}"
            )
            raise ProviderError(f"Cobalt API request failed: {type(e).__name__}: {str(e)}")
