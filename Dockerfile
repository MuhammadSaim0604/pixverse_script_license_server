FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . ./license_server

# Create data directory
RUN mkdir -p license_server/data

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:5000/api/ping')" || exit 1

# Run with gunicorn
CMD ["gunicorn", "--workers=2", "--worker-class=sync", "--timeout=30", "--bind=0.0.0.0:5000", "license_server.app:app"]
