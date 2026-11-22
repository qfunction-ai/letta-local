from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class CheckPasswordMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, password: str):
        super().__init__(app)
        self.password = password

    async def dispatch(self, request, call_next):
        # Exclude health/readiness probe endpoints from password protection
        if request.url.path in {
            "/v1/health",
            "/v1/health/",
            "/latest/health/",
            "/v1/ready",
            "/v1/ready/",
            "/latest/ready",
            "/latest/ready/",
        }:
            return await call_next(request)

        import hmac

        if hmac.compare_digest(
            request.headers.get("X-BARE-PASSWORD") or "", f"password {self.password}"
        ) or hmac.compare_digest(request.headers.get("Authorization") or "", f"Bearer {self.password}"):
            return await call_next(request)

        return JSONResponse(
            content={"detail": "Unauthorized"},
            status_code=401,
        )
