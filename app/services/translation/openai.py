"""
OpenAI翻译服务
"""

from typing import Optional
import httpx
import json
from loguru import logger

from ...core.config import settings
from ...utils.validators import is_chinese_text


class TranslationService:
    """
    OpenAI翻译服务类
    """

    def __init__(self):
        """初始化翻译服务"""
        self.api_key = settings.OPENAI_API_KEY
        self.api_base = settings.OPENAI_API_BASE
        self.model = settings.OPENAI_MODEL
        self.enabled = settings.TRANSLATION_ENABLED and bool(self.api_key)

        if not self.enabled:
            logger.warning("翻译服务未启用或缺少API密钥")

    async def translate_to_chinese(self, text: str) -> str:
        """
        将文本翻译成中文

        Args:
            text: 待翻译的文本

        Returns:
            str: 翻译后的中文文本，如果翻译失败或文本已是中文则返回原文本
        """
        if not text or not text.strip():
            return text

        # 检查是否已经是中文
        if self.is_chinese(text):
            logger.debug("文本已是中文，跳过翻译")
            return text

        # 检查翻译服务是否可用
        if not self.enabled:
            logger.debug("翻译服务未启用，返回原文本")
            return text

        try:
            # 构建翻译提示词
            prompt = self._build_translation_prompt(text)

            # 构建请求数据
            request_data = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一个专业的翻译助手，专门将各种语言的视频描述翻译成自然流畅的中文。"
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "max_tokens": 500,
                "temperature": 0.3,
            }

            # 调用OpenAI API
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json=request_data
                )

                if response.status_code == 200:
                    response_data = response.json()
                    translated_text = response_data["choices"][0]["message"]["content"].strip()

                    # 验证翻译结果
                    if translated_text and len(translated_text) > 0:
                        logger.info(f"翻译成功: {text[:50]}... -> {translated_text[:50]}...")
                        return translated_text
                    else:
                        logger.warning("翻译结果为空，返回原文本")
                        return text
                else:
                    logger.error(f"OpenAI API请求失败: {response.status_code} - {response.text}")
                    return text

        except Exception as e:
            logger.error(f"翻译失败: {e}")
            return text

    def is_chinese(self, text: str) -> bool:
        """
        检测文本是否为中文

        Args:
            text: 待检测的文本

        Returns:
            bool: 如果文本主要是中文返回True，否则返回False
        """
        return is_chinese_text(text)

    def _build_translation_prompt(self, text: str) -> str:
        """
        构建翻译提示词

        Args:
            text: 待翻译的文本

        Returns:
            str: 完整的提示词
        """
        return f"""请将以下视频描述翻译成自然流畅的中文：

原文：
{text}

要求：
1. 保持原意不变
2. 语言自然流畅
3. 适合中文用户理解
4. 如果包含网络用语或表情符号，请适当保留或转换为中文对应表达
5. 只返回翻译结果，不要包含任何解释或额外内容

翻译："""