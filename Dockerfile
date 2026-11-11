FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 ENV=production AUTO_MIGRATE=0 FRONTEND_DIR=/srv/frontend UPLOAD_DIR=/data/uploads RELEASES_DIR=/data/releases
RUN useradd --create-home --uid 10001 fieldwise && mkdir -p /data/uploads && chown -R fieldwise /data
WORKDIR /srv/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY frontend /srv/frontend
USER fieldwise
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/ready', timeout=4).status==200 else 1)"
# One process: live-location streaming and rate limits are per process (see deploy/RUNBOOK.md before scaling out). Migrations run first.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
