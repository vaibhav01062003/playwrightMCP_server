# Playwright MCP Wrapper Server (Python)

A **production-ready Python MCP wrapper server** that proxies Azure Foundry Agent
tool calls through a managed Playwright browser with full observability,
retry resilience, and authentication lifecycle management.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Azure Foundry Agent                         │
└────────────────────────────┬────────────────────────────────────┘
                             │  JSON-RPC (MCP protocol)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              Python Wrapper Server  (app.py)                    │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │  ToolRouter  │──▶│PlaywrightWrap│──▶│  RetryEngine     │    │
│  │  (mcp/)      │   │  per (mcp/)  │   │  (core/)         │    │
│  └──────────────┘   └──────┬───────┘   └──────────────────┘    │
│                            │                                    │
│                    ┌───────▼────────┐   ┌──────────────────┐   │
│                    │   AuthGuard    │   │   Telemetry      │   │
│                    │   (core/)      │   │   (core/)        │   │
│                    └───────┬────────┘   └──────────────────┘   │
│                            │                                    │
│                    ┌───────▼────────┐                           │
│                    │ BrowserManager │                           │
│                    │ (browser/)     │                           │
│                    └───────┬────────┘                           │
└────────────────────────────┼────────────────────────────────────┘
                             │  subprocess stdio (JSON-RPC)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│          npx @playwright/mcp@latest  (MCP subprocess)           │
└────────────────────────────┬────────────────────────────────────┘
                             │  Playwright API
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Chromium Browser                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
playwright-mcp-wrapper/
│
├── app.py                        # Entry point (stdio or HTTP mode)
├── requirements.txt
├── Dockerfile
├── .env.example
├── pytest.ini
│
├── core/
│   ├── __init__.py
│   ├── config.py                 # Env-driven configuration (no hardcoded secrets)
│   ├── logger.py                 # Structured JSON logging (structlog)
│   ├── retry_engine.py           # Async exponential-backoff retry system
│   ├── telemetry.py              # OpenTelemetry + Azure App Insights
│   └── auth_guard.py             # Session / authentication lifecycle
│
├── mcp/
│   ├── __init__.py
│   ├── playwright_process.py     # npx subprocess manager (JSON-RPC over stdio)
│   ├── playwright_wrapper.py     # Middleware stack (auth → retry → telemetry)
│   └── tool_router.py            # JSON-RPC dispatcher
│
├── browser/
│   ├── __init__.py
│   ├── browser_manager.py        # Playwright browser/context/page lifecycle
│   ├── login_handler.py          # Site-specific login helpers
│   └── recovery.py               # Crash recovery utilities
│
└── tests/
    ├── __init__.py
    ├── test_retry.py
    ├── test_auth.py
    └── test_wrapper.py
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `HARNESS_RETRY_COUNT` | `3` | Max retry attempts |
| `HARNESS_RETRY_DELAY` | `1.5` | Base delay in seconds (exponential backoff) |
| `HARNESS_TIMEOUT` | `30.0` | Per-attempt timeout in seconds |
| `OTEL_ENABLED` | `false` | Enable OpenTelemetry |
| `OTEL_SERVICE_NAME` | `playwright-mcp-wrapper` | Service name in traces |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | — | Azure App Insights connection string |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `DEBUG` | `false` | Enable human-readable console logs |
| `PLAYWRIGHT_HEADLESS` | `true` | Run browser headless |
| `PLAYWRIGHT_MCP_COMMAND` | `npx` | Command to launch Playwright MCP |
| `PLAYWRIGHT_MCP_ARGS` | `@playwright/mcp@latest` | Args for the command |
| `LOGIN_URL` | — | URL of the login page |
| `SCHOOLOGY_EMAIL` | — | Login email |
| `SCHOOLOGY_PASSWORD` | — | Login password |
| `SESSION_TTL_SECONDS` | `3600` | Session expiry (seconds) |
| `SERVER_MODE` | `stdio` | `stdio` or `http` |
| `SERVER_HOST` | `0.0.0.0` | HTTP server host (http mode) |
| `SERVER_PORT` | `8000` | HTTP server port (http mode) |

---

## Local Development

### Prerequisites

- Python 3.11+
- Node.js 20+ (for `npx @playwright/mcp`)
- Docker (optional)

### Setup

```bash
# Clone / enter directory
cd playwright-mcp-wrapper

# Create virtual environment
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
playwright install-deps chromium

# Configure environment
cp .env.example .env
# Edit .env with your values

# Run in stdio mode (default)
python app.py

# Run in HTTP mode
SERVER_MODE=http python app.py
```

### Running Tests

```bash
pytest tests/ -v
```

---

## Docker Build & Run

```bash
# Build image
docker build -t playwright-mcp-wrapper:latest .

# Run in stdio mode
docker run --rm -i \
  --env-file .env \
  playwright-mcp-wrapper:latest

# Run in HTTP mode
docker run --rm -p 8000:8000 \
  --env-file .env \
  -e SERVER_MODE=http \
  playwright-mcp-wrapper:latest

# Health check
curl http://localhost:8000/health
```

---

## Azure Container Apps Deployment

### 1. Push image to Azure Container Registry

```bash
ACR_NAME=myregistry
IMAGE_TAG=playwright-mcp-wrapper:latest

az acr login --name $ACR_NAME
docker tag playwright-mcp-wrapper:latest $ACR_NAME.azurecr.io/$IMAGE_TAG
docker push $ACR_NAME.azurecr.io/$IMAGE_TAG
```

### 2. Create Container App

```bash
RESOURCE_GROUP=my-rg
ENVIRONMENT=my-aca-env
APP_NAME=playwright-mcp-wrapper

az containerapp create \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --environment $ENVIRONMENT \
  --image $ACR_NAME.azurecr.io/$IMAGE_TAG \
  --registry-server $ACR_NAME.azurecr.io \
  --env-vars \
      SERVER_MODE=http \
      PLAYWRIGHT_HEADLESS=true \
      OTEL_ENABLED=true \
      LOG_LEVEL=INFO \
      HARNESS_RETRY_COUNT=3 \
      APPLICATIONINSIGHTS_CONNECTION_STRING=secretref:appinsights-conn \
      SCHOOLOGY_EMAIL=secretref:schoology-email \
      SCHOOLOGY_PASSWORD=secretref:schoology-password \
  --secrets \
      appinsights-conn="<YOUR_CONNECTION_STRING>" \
      schoology-email="<EMAIL>" \
      schoology-password="<PASSWORD>" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 3 \
  --cpu 1.0 \
  --memory 2.0Gi
```

### 3. Configure scaling rule (optional)

```bash
az containerapp update \
  --name $APP_NAME \
  --resource-group $RESOURCE_GROUP \
  --scale-rule-name http-rule \
  --scale-rule-type http \
  --scale-rule-metadata concurrentRequests=10
```

---

## Azure Foundry Integration

### MCP Server Registration (stdio mode — local/agent)

```json
{
  "mcpServers": {
    "playwright-python-wrapper": {
      "command": "python",
      "args": ["app.py"],
      "env": {
        "LOGIN_URL": "https://app.schoology.com/login",
        "SCHOOLOGY_EMAIL": "${SCHOOLOGY_EMAIL}",
        "SCHOOLOGY_PASSWORD": "${SCHOOLOGY_PASSWORD}",
        "PLAYWRIGHT_HEADLESS": "true",
        "LOG_LEVEL": "INFO"
      }
    }
  }
}
```

### MCP Server Registration (HTTP mode — Azure Container Apps)

```json
{
  "mcpServers": {
    "playwright-python-wrapper": {
      "url": "https://<your-container-app>.azurecontainerapps.io/mcp",
      "transport": "http"
    }
  }
}
```

---

## Middleware Stack (per tool call)

```
Incoming MCP tool/call request
        │
        ▼
 ToolRouter.handle()          — parse JSON-RPC, dispatch
        │
        ▼
 PlaywrightWrapper.call_tool()
        │
        ├─► Telemetry span open
        │
        ├─► AuthGuard.ensure_login()
        │       └─► validate session TTL
        │           validate cookies
        │           validate browser state
        │           if invalid → RetryEngine(login)
        │
        ├─► _normalise_screenshot_args()  — prevent fullPage+selector conflict
        │
        ├─► RetryEngine.execute(MCP_POLICY)
        │       └─► PlaywrightMCPProcess.call_tool()
        │               └─► JSON-RPC to npx subprocess
        │                       └─► Playwright → Chromium
        │
        ├─► Capture result
        │
        └─► Telemetry span close + metrics (latency, success, errors)
```

---

## Retry Policies

| Policy | Max Attempts | Base Delay | Use Case |
|---|---|---|---|
| `BROWSER_POLICY` | 4 | 2.0s | Browser launch / navigation failures |
| `MCP_POLICY` | 3 | 1.0s | MCP tool call failures |
| `NETWORK_POLICY` | 5 | 0.5s | Network / HTTP errors |
| `AUTH_POLICY` | 3 | 3.0s | Login failures |
| `DEFAULT_POLICY` | env | env | Everything else |

---

## Telemetry Spans

| Span Name | Description |
|---|---|
| `mcp.tool_call` | Full tool invocation including auth + retry |
| `auth_guard.ensure_login` | Auth validation gate |
| `auth_guard.perform_login` | Active login execution |
| `browser.recovery` | Browser crash recovery |

### Metrics

| Metric | Type | Description |
|---|---|---|
| `mcp.tool_calls` | Counter | Total tool invocations |
| `mcp.tool_errors` | Counter | Total tool failures |
| `mcp.tool_latency_ms` | Histogram | Tool execution latency |
| `auth.events` | Counter | Auth events (login/relogin/failure) |
| `retry.attempts` | Counter | Retry attempts by policy |
| `browser.crashes` | Counter | Browser crash/recovery events |

---

## Testing Guide

```bash
# All tests
pytest tests/ -v

# Specific module
pytest tests/test_retry.py -v
pytest tests/test_auth.py -v
pytest tests/test_wrapper.py -v

# With coverage
pip install pytest-cov
pytest tests/ --cov=core --cov=mcp --cov=browser --cov-report=term-missing
```

Test coverage areas:
- `test_retry.py` — transient error detection, backoff math, timeout protection, exhaustion
- `test_auth.py` — session validation, TTL expiry, cookie checks, invalidation
- `test_wrapper.py` — JSON-RPC routing, tool call proxying, screenshot normalisation

---

## Security Notes

- All secrets are environment variables — never committed to source
- Docker image runs as non-root user `appuser` (UID 1001)
- No secrets in image layers
- Azure Container Apps secrets stored in secret store, referenced by name
- `--no-sandbox` Chromium flag required inside containers (standard practice)
