FROM python:3.12-slim AS base

# Minimal runtime. No build tools in the final image.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first for layer caching.
COPY pyproject.toml README.md ./
COPY meshcore_rpc_services ./meshcore_rpc_services
RUN pip install --no-cache-dir .

# Database lives on a mounted volume by convention; create the directory so
# SqliteStore can write without extra mkdir surprises.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV MESHCORE_RPC_SERVICES_SERVICE__DB_PATH=/app/data/meshcore_rpc_services.sqlite3 \
    MESHCORE_RPC_SERVICES_MQTT__HOST=mosquitto

ENTRYPOINT ["meshcore-rpc-services"]
CMD ["run"]
