FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY frontend ./frontend

EXPOSE 8080
CMD ["sh", "-c", "cd /app/backend && exec uvicorn app.main:app --host 0.0.0.0 --port 8080]

