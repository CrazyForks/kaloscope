# ============================================================
# Stage 1: build the frontend
# ============================================================
FROM node:25-slim AS frontend

# install pnpm via npm
RUN npm install -g pnpm

WORKDIR /pages

# copy dependency manifests first for better layer caching
COPY frontend/.npmrc frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./

# install frontend dependencies
RUN pnpm install --frozen-lockfile

# copy the rest of the frontend source code
COPY frontend/ ./

# build the static pages for production
RUN pnpm run build

# ============================================================
# Stage 2: build the production image
# ============================================================
FROM --platform=linux/amd64 python:3.13-slim

# install runtime dependencies
# - git: required by gitpython
# - libxml2/libxslt: required by lxml
# - gosu: for dropping privileges if needed
# - aria2: optional built-in download manager
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libxml2 \
    libxslt1.1 \
    gosu \
    aria2 \
    && rm -rf /var/lib/apt/lists/*

# install poetry via pip
ENV POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true
RUN python -m pip install --no-cache-dir setuptools poetry

WORKDIR /app

# copy backend dependency manifests first for better layer caching
COPY backend/pyproject.toml backend/poetry.lock backend/poetry.toml ./backend/

# install backend dependencies, then remove build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake make g++ \
    && cd backend && poetry install --no-cache --no-root --only main \
    && apt-get purge -y cmake make g++ \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# copy the rest of the backend source code
COPY backend/ ./backend/

# copy the built frontend assets from the previous stage
COPY --from=frontend /pages/build/ ./frontend/build/

# environment variables
ENV PUID=0
ENV PGID=0
ENV UMASK=022
ENV ENABLE_ARIA2=false

# entrypoint script
COPY <<'EOF' /app/entrypoint.sh
#!/bin/sh
set -e
cd /app/backend

# apply umask
umask ${UMASK:-022}

# setup user if PUID/PGID is set
PUID=${PUID:-0}
PGID=${PGID:-0}
if [ "$PUID" != "0" ] && [ "$(id -u)" = "0" ]; then
  getent group "$PGID" > /dev/null 2>&1 || groupadd -g "$PGID" kaloscope
  id -u "$PUID" > /dev/null 2>&1 || useradd -u "$PUID" -g "$PGID" -m -s /bin/sh kaloscope
  chown -R "$PUID":"$PGID" /app
  exec gosu "$PUID" "$0" "$@"
fi

# start aria2 if enabled
if [ "$ENABLE_ARIA2" = "true" ]; then
  mkdir -p /app/workspace/downloads
  ARIA2_SESSION=/app/workspace/aria2.session
  touch "$ARIA2_SESSION"
  aria2c \
    --enable-rpc \
    --rpc-listen-all=false \
    --rpc-listen-port=6800 \
    --enable-peer-exchange=true \
    --enable-dht=true \
    --dht-listen-port=6888 \
    --listen-port=6888 \
    --input-file="$ARIA2_SESSION" \
    --save-session="$ARIA2_SESSION" \
    --save-session-interval=60 \
    --dir=/app/workspace/downloads \
    --daemon
fi

# start sanic web server
exec poetry run sanic app.main:app --host 0.0.0.0 --port 8000 --fast
EOF
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
EXPOSE 6888
EXPOSE 6888/udp
VOLUME /app/workspace
ENTRYPOINT ["/app/entrypoint.sh"]
