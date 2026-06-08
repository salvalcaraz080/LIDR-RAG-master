# =============================================================================
# Stage 1 — Builder
# =============================================================================
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests first for layer caching (same rationale as backend).
COPY pyproject.toml uv.lock* ./

# Install ONLY the frontend group. --no-default-groups skips the backend deps
# (fastapi, openai, anthropic) that the frontend doesn't need — this is the
# component separation we've maintained, enforced at the image level.
RUN uv sync --frozen --no-install-project --no-default-groups --group frontend


# =============================================================================
# Stage 2 — Runtime
# =============================================================================
FROM python:3.11-slim AS runtime

RUN groupadd --system appgroup && \
    useradd --system --gid appgroup --create-home appuser

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# Copy only the frontend source — not the backend's app/ directory.
COPY frontend/ /app/frontend/

RUN chown -R appuser:appgroup /app

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8501

# Streamlit needs --server.address=0.0.0.0 so it accepts connections from
# outside the container (the host's browser), same principle as Uvicorn's --host.
CMD ["streamlit", "run", "frontend/streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.port=8501"]