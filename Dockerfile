FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY crypto_bot ./crypto_bot
COPY main.py bot.py ./

RUN useradd -m -u 10001 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["python", "-m", "crypto_bot.main"]
