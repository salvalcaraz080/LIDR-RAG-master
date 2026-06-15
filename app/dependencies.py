from fastapi import Request
from redisvl.extensions.cache.llm import SemanticCache


def get_semantic_cache(request: Request) -> SemanticCache:
    """Dependency provider: hands the app-wide semantic cache to endpoints."""
    return request.app.state.semantic_cache
