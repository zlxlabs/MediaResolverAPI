"""
HTTP客户端工具模块
封装httpx客户端，提供统一的HTTP请求接口
"""

import asyncio
from typing import Optional, Dict, Any
import httpx
from loguru import logger

from ..core.config import settings


class HTTPClient:
    """
    异步HTTP客户端封装类
    提供重试机制和统一的错误处理
    """

    def __init__(self, timeout: int = 30, max_retries: int = 3):
        """
        初始化HTTP客户端

        Args:
            timeout: 请求超时时间（秒）
            max_retries: 最大重试次数
        """
        self.timeout = timeout
        self.max_retries = max_retries
        self.client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    async def get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> httpx.Response:
        """
        发送GET请求

        Args:
            url: 请求URL
            headers: 请求头
            params: 查询参数
            **kwargs: 其他httpx参数

        Returns:
            httpx.Response: 响应对象

        Raises:
            httpx.HTTPError: HTTP请求错误
        """
        return await self._request("GET", url, headers=headers, params=params, **kwargs)

    async def post(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> httpx.Response:
        """
        发送POST请求

        Args:
            url: 请求URL
            headers: 请求头
            json: JSON数据
            data: 表单数据
            **kwargs: 其他httpx参数

        Returns:
            httpx.Response: 响应对象

        Raises:
            httpx.HTTPError: HTTP请求错误
        """
        return await self._request("POST", url, headers=headers, json=json, data=data, **kwargs)

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        发送HTTP请求的内部方法，包含重试逻辑

        Args:
            method: HTTP方法
            url: 请求URL
            **kwargs: 请求参数

        Returns:
            httpx.Response: 响应对象

        Raises:
            httpx.HTTPError: HTTP请求错误
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                if attempt > 0:  # 只在重试时记录日志
                    logger.debug(f"重试{method}请求到 {url} (尝试 {attempt + 1}/{self.max_retries + 1})")
                response = await self.client.request(method, url, **kwargs)
                response.raise_for_status()
                return response

            except httpx.HTTPError as e:
                last_exception = e

                # 检查是否为不应该重试的错误类型
                if hasattr(e, 'response') and e.response is not None:
                    status_code = e.response.status_code
                    # 4xx 客户端错误通常不应该重试（除了429和某些特殊情况）
                    if 400 <= status_code < 500 and status_code not in [408, 429]:
                        if status_code == 401:
                            logger.error(f"认证失败(401)，请检查API Token: {e}")
                        elif status_code == 403:
                            logger.error(f"权限不足(403)，请检查访问权限: {e}")
                        else:
                            logger.error(f"客户端错误({status_code})，不进行重试: {e}")
                        break  # 立即停止重试

                if attempt < self.max_retries:
                    wait_time = 2 ** attempt  # 指数退避
                    logger.warning(f"请求失败，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"请求最终失败: {e}")

        # 如果所有重试都失败了，抛出最后一个异常
        raise last_exception


# Global HTTP client instance
http_client = HTTPClient(
    timeout=settings.HTTP_TIMEOUT_SECONDS,
    max_retries=3,
)