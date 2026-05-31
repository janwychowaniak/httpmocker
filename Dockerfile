# Pinned by digest for reproducible, tamper-evident builds. The tag is kept
# in the comment for readability; Dependabot's docker ecosystem updates both.
FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203

# Set working directory
WORKDIR /app

# Create dedicated user for security
RUN groupadd -r httpmocker && useradd -r -g httpmocker httpmocker

# Install the application and its runtime dependencies from pyproject.toml
# (the single source of truth). The source is copied first because the version
# is read dynamically from httpmocker/__init__.py at build time.
COPY pyproject.toml README.md ./
COPY httpmocker/ ./httpmocker/
RUN pip install --no-cache-dir .

# Create directory for mounted payload volumes
RUN mkdir -p /app/payloads && \
    chown -R httpmocker:httpmocker /app

# Switch to non-root user
USER httpmocker

# Expose port 8080
EXPOSE 8080

# Verify the server is accepting connections on the exposed port
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import socket, sys; s = socket.socket(); s.settimeout(2); sys.exit(s.connect_ex(('127.0.0.1', 8080)))"

# Default command (mount your own config over /app/config.json)
CMD ["httpmocker", "-p", "8080", "-c", "/app/config.json"]
