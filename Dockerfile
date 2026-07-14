FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create data directory
RUN mkdir -p data

# Expose port (Railway will assign actual port via $PORT env var)
EXPOSE 5000

# Health check (using urllib instead of requests - more minimal)
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/ping')" || exit 1

# Run with gunicorn (correct module path: app is at /app/app.py)
CMD ["gunicorn", "--workers=2", "--worker-class=sync", "--timeout=30", "--bind=0.0.0.0:5000", "app:app"]
