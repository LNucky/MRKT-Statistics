FROM python:3.12-slim-bookworm

# Совпадают с владельцем каталога ./data на хосте (bind mount перекрывает /data в образе).
ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OUTPUT_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper.py parser.py ./

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid app --create-home app \
    && mkdir -p /data \
    && chown app:app /data

USER app

ENTRYPOINT ["python"]
CMD ["scraper.py"]
