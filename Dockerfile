FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# Create data directory for SQLite database and logs
RUN mkdir -p /data

EXPOSE 5000

# Use gunicorn for production with thread workers
# --threads 4 allows concurrent SSE connections per worker
# --timeout 120 prevents SSE connections from timing out
CMD ["gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--chdir", "/app/backend", \
     "main:app"]
