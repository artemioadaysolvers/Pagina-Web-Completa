FROM python:3.14.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DB_BACKEND=sqlite \
    SQLITE_PATH=/app/data/sumas.sqlite3

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && mkdir -p /app/data \
    && chown -R 10001:10001 /app/data

COPY server.py index.html styles.css app.js favicon.svg ./
USER 10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3)" || exit 1
CMD ["python", "server.py"]
