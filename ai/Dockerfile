FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app:/app/backend

WORKDIR /app

COPY backend/requirements.txt /tmp/backend-requirements.txt
COPY ai/requirements.txt /tmp/ai-requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/backend-requirements.txt -r /tmp/ai-requirements.txt

COPY backend/ /app/backend/
COPY ai/ /app/ai/
COPY data/ /app/data/
COPY models/ /app/models/

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/health', timeout=3)" || exit 1

CMD ["uvicorn", "ai.inference.server:app", "--host", "0.0.0.0", "--port", "8100"]
