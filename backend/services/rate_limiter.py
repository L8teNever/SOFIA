import time
from collections import defaultdict
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    In-memory sliding window rate limiter with generous limits designed to never
    impede legitimate usage while preventing automated floods/spam.
    """

    def __init__(self, app):
        super().__init__(app)
        # Store timestamps of requests: key -> list of float timestamps
        self.requests = defaultdict(list)

    def _clean_old(self, key: str, window: float, now: float):
        cutoff = now - window
        self.requests[key] = [t for t in self.requests[key] if t > cutoff]

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Ignore static assets, SW, icons, and healthchecks
        if (
            path.startswith("/static")
            or path.startswith("/uploads")
            or path.endswith(".js")
            or path.endswith(".css")
            or path.endswith(".png")
            or path.endswith(".ico")
            or path.endswith(".svg")
            or path == "/sw.js"
            or path == "/favicon.ico"
        ):
            return await call_next(request)

        # Determine client identifier (IP)
        client_ip = request.headers.get("x-forwarded-for")
        if client_ip:
            client_ip = client_ip.split(",")[0].strip()
        elif request.client:
            client_ip = request.client.host
        else:
            client_ip = "127.0.0.1"

        now = time.time()
        window = 60.0  # 1 minute

        # Configure generous limits
        if path.startswith("/api/v1/auth/login"):
            limit = 30  # 30 login attempts / minute
            bucket_key = f"auth:{client_ip}"
        elif "/upload" in path or (path.startswith("/api/v1/files") and request.method == "POST"):
            limit = 30  # 30 file uploads / minute (up to 1 GB per file)
            bucket_key = f"upload:{client_ip}"
        elif path.startswith("/api/v1/"):
            limit = 200  # 200 API requests / minute (very generous)
            bucket_key = f"api:{client_ip}"
        else:
            limit = 300  # 300 general page hits / minute
            bucket_key = f"page:{client_ip}"

        self._clean_old(bucket_key, window, now)
        current_count = len(self.requests[bucket_key])

        if current_count >= limit:
            retry_after = int(window - (now - self.requests[bucket_key][0])) + 1
            response = JSONResponse(
                status_code=429,
                content={"detail": "Zu viele Anfragen. Bitte warte einen Moment."},
                headers={
                    "Retry-After": str(max(1, retry_after)),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
            return response

        self.requests[bucket_key].append(now)
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, limit - current_count - 1))
        return response
