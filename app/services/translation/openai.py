"""OpenAI-compatible translation service with honest failure handling."""

from dataclasses import dataclass
from enum import Enum
import time
from typing import Any, Optional

import httpx
import sentry_sdk
from loguru import logger

from ...core.config import settings
from ...utils.validators import is_chinese_text


class TranslationStatus(str, Enum):
    """翻译结果状态。"""

    ok = "ok"
    skipped_chinese = "skipped_chinese"
    skipped_disabled = "skipped_disabled"
    skipped_not_requested = "skipped_not_requested"
    failed = "failed"


@dataclass(frozen=True)
class TranslationResult:
    """翻译结果；只有成功结果才携带译文。"""

    status: TranslationStatus
    text: Optional[str]
    model: Optional[str] = None


def report_translation_unavailable(models: list[str], error_code: str) -> None:
    """向 Sentry/GlitchTip 报告翻译上游熔断。"""
    message = (
        f"翻译上游不可用：已尝试模型 {', '.join(models)}，最后错误码 {error_code}。"
        "请检查网关渠道或改 OPENAI_MODEL / OPENAI_MODEL_FALLBACKS 后重启 media-resolver-api"
    )
    try:
        sentry_sdk.capture_message(
            message,
            level="error",
            fingerprint=["translation_upstream_unavailable"],
        )
    except Exception as exc:
        logger.warning("翻译不可用告警上报失败: {}", type(exc).__name__)


class TranslationService:
    """OpenAI-compatible translation service."""

    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.api_base = settings.OPENAI_API_BASE
        self.model = settings.OPENAI_MODEL
        self.models = self._configured_models()
        self.enabled = settings.TRANSLATION_ENABLED and bool(self.api_key)
        self.circuit_cooldown_seconds = float(
            settings.TRANSLATION_CIRCUIT_COOLDOWN_SECONDS
        )
        self._dead_models: dict[str, float] = {}
        self._circuit_opened_at: Optional[float] = None

        if not self.enabled:
            logger.warning("翻译服务未启用或缺少API密钥")

    @staticmethod
    def _configured_models() -> list[str]:
        configured = [settings.OPENAI_MODEL]
        configured.extend(
            model.strip()
            for model in settings.OPENAI_MODEL_FALLBACKS.split(",")
            if model.strip()
        )
        return list(dict.fromkeys(model for model in configured if model))

    @property
    def circuit_open(self) -> bool:
        """熔断是否处于冷却期。"""
        if self._circuit_opened_at is None:
            return False
        if time.monotonic() - self._circuit_opened_at >= self.circuit_cooldown_seconds:
            self._circuit_opened_at = None
            return False
        return True

    @property
    def health_status(self) -> str:
        """返回翻译子系统健康状态。"""
        if not self.enabled:
            return "disabled"
        return "unavailable" if self.circuit_open else "ok"

    async def translate_to_chinese(self, text: str) -> TranslationResult:
        """把文本翻译成中文，并以结构化状态返回结果。"""
        if not text or not text.strip() or self.is_chinese(text):
            logger.debug("文本为空或已是中文，跳过翻译")
            return TranslationResult(TranslationStatus.skipped_chinese, None)

        if not self.enabled:
            logger.debug("翻译服务未启用")
            return TranslationResult(TranslationStatus.skipped_disabled, None)

        if self.circuit_open:
            logger.warning("翻译熔断开启，跳过上游请求")
            return TranslationResult(TranslationStatus.failed, None)

        attempted_models: list[str] = []
        last_error_code = "unknown"
        for model in self.models:
            if self._model_in_cooldown(model):
                continue

            attempted_models.append(model)
            request_data = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "你是一个专业的翻译助手，专门将各种语言的视频描述翻译成自然流畅的中文。",
                    },
                    {"role": "user", "content": self._build_translation_prompt(text)},
                ],
                "max_tokens": 500,
                "temperature": 0.3,
            }

            try:
                response = await self._request(request_data)
            except Exception as exc:
                last_error_code = type(exc).__name__
                logger.error("翻译请求异常: model={}, error={}", model, last_error_code)
                continue

            if response.status_code == 200:
                translated_text = self._translated_text(response)
                if translated_text:
                    logger.info("翻译成功: model={}", model)
                    return TranslationResult(
                        TranslationStatus.ok, translated_text, model
                    )
                last_error_code = "empty_translation"
                logger.warning("翻译结果为空: model={}", model)
                continue

            last_error_code = self._error_code(response)
            logger.error(
                "OpenAI API请求失败: model={}, status={}, code={}",
                model,
                response.status_code,
                last_error_code,
            )
            if self._is_model_unavailable(response, last_error_code):
                self._dead_models[model] = (
                    time.monotonic() + self.circuit_cooldown_seconds
                )

        if self._all_models_dead():
            self._open_circuit(attempted_models or self.models, last_error_code)
        return TranslationResult(TranslationStatus.failed, None)

    async def _request(self, request_data: dict[str, Any]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=request_data,
            )

    def _model_in_cooldown(self, model: str) -> bool:
        expires_at = self._dead_models.get(model)
        if expires_at is None:
            return False
        if time.monotonic() >= expires_at:
            del self._dead_models[model]
            return False
        return True

    def _all_models_dead(self) -> bool:
        return bool(self.models) and all(
            self._model_in_cooldown(model) for model in self.models
        )

    def _open_circuit(self, models: list[str], error_code: str) -> None:
        if self._circuit_opened_at is not None and self.circuit_open:
            return
        self._circuit_opened_at = time.monotonic()
        report_translation_unavailable(models, error_code)

    @staticmethod
    def _translated_text(response: httpx.Response) -> Optional[str]:
        try:
            value = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return value.strip() if isinstance(value, str) else None

    @staticmethod
    def _error_details(
        response: httpx.Response,
    ) -> tuple[Optional[str], Optional[str], str]:
        try:
            error = response.json().get("error", {})
        except (TypeError, ValueError):
            error = {}
        if not isinstance(error, dict):
            error = {}
        code = error.get("code")
        error_type = error.get("type")
        message = error.get("message") or ""
        return code, error_type, message if isinstance(message, str) else ""

    @classmethod
    def _error_code(cls, response: httpx.Response) -> str:
        code, error_type, _ = cls._error_details(response)
        return str(code or error_type or f"http_{response.status_code}")

    @classmethod
    def _is_model_unavailable(
        cls, response: httpx.Response, error_code: str
    ) -> bool:
        code, error_type, message = cls._error_details(response)
        return (
            code == "model_not_found"
            or error_code == "model_not_found"
            or (error_type == "new_api_error" and "无可用渠道" in message)
            or (
                error_type == "new_api_error"
                and "no available channel" in message.lower()
            )
        )

    def is_chinese(self, text: str) -> bool:
        """判断文本是否主要是中文。"""
        return is_chinese_text(text)

    def _build_translation_prompt(self, text: str) -> str:
        """构建翻译提示词。"""
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


async def probe_translation_upstream(service: TranslationService) -> TranslationResult:
    """启动时探测翻译上游，失败仅影响翻译熔断状态。"""
    result = await service.translate_to_chinese("translation startup probe")
    if result.status == TranslationStatus.failed and not service.circuit_open:
        service._open_circuit(service.models, "startup_probe_failed")
    return result
