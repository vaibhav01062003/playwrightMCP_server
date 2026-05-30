# ─────────────────────────────────────────────────────────────────────────────
# Playwright MCP Wrapper Server — Production Dockerfile
# Target: Azure Container Apps (Linux/amd64)
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Node.js base (needed for npx @playwright/mcp) ───────────────────
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy AS base

# Install Node.js LTS (needed to run `npx @playwright/mcp@latest`)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npm@latest \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Stage 2: Python dependencies ─────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (chromium only for smaller image)
RUN playwright install chromium \
    && playwright install-deps chromium

# Pre-cache npx @playwright/mcp so cold starts are fast
RUN npx --yes @playwright/mcp@latest --version || true

# ── Stage 3: Copy application ─────────────────────────────────────────────────
COPY . .

# Create non-root user for security
RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/bash --create-home appuser \
    && chown -R appuser:appgroup /app

USER appuser

# ── Runtime ───────────────────────────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_HEADLESS=true \
    SERVER_MODE=stdio

# Expose port for HTTP mode
EXPOSE 8000

# Healthcheck (only relevant in HTTP mode)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["python", "app.py"]
