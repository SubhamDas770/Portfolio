FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=3000 \
    PORTFOLIO_DATABASE_PATH=/data/visitor_leads.sqlite3

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --uid 10001 --gid app --create-home app \
    && install -d --owner=app --group=app /data

COPY requirements.txt .
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app backend ./backend
COPY --chown=app:app public ./public

USER app:app

VOLUME ["/data"]
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/', timeout=2).close()" || exit 1

CMD ["python", "-m", "backend.server"]

# production build


