FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Create dedicated user for security
RUN groupadd -r httpmocker && useradd -r -g httpmocker httpmocker

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY httpmocker/ ./httpmocker/

# Create directories for mounted volumes
RUN mkdir -p /app/payloads && \
    chown -R httpmocker:httpmocker /app

# Switch to non-root user
USER httpmocker

# Expose port 8080
EXPOSE 8080

# Default command
CMD ["python", "-m", "httpmocker", "-p", "8080", "-c", "/app/config.json"]
