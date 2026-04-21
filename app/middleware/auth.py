from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


class ApiKeyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not settings.AUTH_ENABLED:
            return await call_next(request)

        # Health endpoints are always public
        if request.url.path.startswith("/health"):
            return await call_next(request)

        key = request.headers.get("X-API-Key")
        if not key or key != settings.API_KEY:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        return await call_next(request)
