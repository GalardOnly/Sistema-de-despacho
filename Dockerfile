FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DESPACHO_DB_PATH=/tmp/despacho.db

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY corridas ./corridas
COPY alembic.ini ./alembic.ini
COPY database/migrations ./database/migrations
COPY scripts/init_supabase.py ./scripts/init_supabase.py
COPY gunicorn.conf.py ./gunicorn.conf.py

RUN addgroup --system app \
    && adduser --system --ingroup app --home /home/app app

USER app

EXPOSE 8080

CMD ["gunicorn", "--config", "gunicorn.conf.py", "corridas.app:app"]
