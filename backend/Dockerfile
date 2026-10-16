# uv only exists at build time (bind-mounted below), never in the runtime image.
FROM ghcr.io/astral-sh/uv:0.11.26 AS uv

FROM python:3.14-slim

WORKDIR /app

# unixodbc-dev + gcc: needed to build the pyodbc C extension (SQL Server
# connector). This gets pyodbc installable, but actually reaching a SQL Server
# instance also needs Microsoft's ODBC Driver 17/18 registered in this image -
# not added here (proprietary package, separate apt repo + EULA) - the SQL
# Server connector's test() call raises a clear error until that's added.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./
# Locked runtime deps go into /app/.venv (uv venvs ship without pip). The base
# image's own pip is uninstalled too: the app never installs packages at
# runtime, and pip's vendored copies of urllib3/msgpack/setuptools carry CVEs
# that fail the Trivy image scan.
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 PATH="/app/.venv/bin:$PATH"
RUN --mount=from=uv,source=/uv,target=/bin/uv \
    uv sync --frozen --no-dev --no-install-project --no-cache \
    && pip uninstall -y pip

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
