FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY . .

RUN useradd --create-home app
USER app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# gthread: LLM 호출이 수 초 걸려도 다른 요청이 막히지 않게 (설계/시스템-아키텍처.md)
CMD ["gunicorn", "config.wsgi", "--worker-class", "gthread", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8000"]
