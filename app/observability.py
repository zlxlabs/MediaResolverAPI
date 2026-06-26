"""Fail-open GlitchTip/Sentry observability integration."""
import importlib
import logging
import os


logger = logging.getLogger(__name__)

SERVICE_NAME = "media-resolver-api"
REPO_NAME = "zj1123581321/MediaResolverAPI"


def init_observability() -> bool:
    """
    Initialize zlx-ops-sdk when available.

    Deliberately fail-open: missing SDK, missing DSN, bad DSN, or SDK init
    errors must never prevent the API service from starting.
    """
    try:
        zlx_ops_sdk = importlib.import_module("zlx_ops_sdk")
    except ModuleNotFoundError:
        logger.warning("zlx_ops_sdk is not installed; observability disabled")
        return False

    try:
        result = zlx_ops_sdk.init(
            SERVICE_NAME,
            repo=REPO_NAME,
            server=os.environ.get("OPS_SERVER", "fordeal"),
            environment=os.environ.get("APP_ENV", "prod"),
        )
        return bool(getattr(result, "enabled", False))
    except Exception as exc:
        logger.warning("zlx_ops_sdk init failed; observability disabled: %r", exc)
        return False
