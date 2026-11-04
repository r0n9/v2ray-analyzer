FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code and static assets
COPY app ./app
COPY static ./static
COPY config.json ./config.json

# Create default volume directories
RUN mkdir -p /logs /data && chmod 777 /data

# Default environment configurations
ENV PORT=8848 \
    LOG_DIR=/logs \
    ACCESS_LOG_PATH=/logs/access.log \
    DB_PATH=/data/v2ray_stats.db \
    ADMIN_USER=admin \
    ADMIN_PASSWORD=changeme888 \
    RETENTION_DAYS=180

EXPOSE 8848

# Run analyzer service
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8848"]
