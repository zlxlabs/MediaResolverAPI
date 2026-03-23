"""
Shared API dependencies.

Authentication and other cross-cutting concerns for API routes.
"""

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

from ..core.config import settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(_api_key_header)):
    """Verify the API key from request header."""
    if not settings.API_KEY:
        return  # No API key configured, allow all requests
    if not api_key or api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
