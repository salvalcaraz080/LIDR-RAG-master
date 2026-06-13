from fastapi import Request
import redis.asyncio as aredis


def get_redis(request: Request) -> aredis.Redis:
    """Dependency provider: hands the app-wide Redis client to endpoints."""
    return request.app.state.redis