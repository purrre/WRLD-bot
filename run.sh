#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Starting Redis ==="

# Check if Redis is already running
if redis-cli ping 2>/dev/null | grep -q PONG; then
  echo "  Redis is already running."
else
  REDIS_BIN=""
  REDIS_CLI=""

  # Check if redis-server is on PATH
  if command -v redis-server &>/dev/null; then
    REDIS_BIN="redis-server"
    REDIS_CLI="redis-cli"
  else
    # Check local bin
    LOCAL_BIN="$SCRIPT_DIR/.local/bin"
    if [ -x "$LOCAL_BIN/redis-server" ]; then
      REDIS_BIN="$LOCAL_BIN/redis-server"
      REDIS_CLI="$LOCAL_BIN/redis-cli"
    else
      echo "  Downloading static Redis binaries..."
      mkdir -p "$LOCAL_BIN"
      curl -sL "https://github.com/phlummox-dev/redis-static-binaries/releases/download/6.2.5.0/redis-server" \
        -o "$LOCAL_BIN/redis-server" && chmod +x "$LOCAL_BIN/redis-server"
      curl -sL "https://github.com/phlummox-dev/redis-static-binaries/releases/download/6.2.5.0/redis-cli" \
        -o "$LOCAL_BIN/redis-cli" && chmod +x "$LOCAL_BIN/redis-cli"

      if [ -x "$LOCAL_BIN/redis-server" ]; then
        REDIS_BIN="$LOCAL_BIN/redis-server"
        REDIS_CLI="$LOCAL_BIN/redis-cli"
      fi
    fi
  fi

  if [ -n "$REDIS_BIN" ]; then
    echo "  Starting Redis: $REDIS_BIN"
    "$REDIS_BIN" --daemonize yes --port 6379 --bind 127.0.0.1 \
      --maxmemory 256mb --maxmemory-policy allkeys-lru \
      --dir "$SCRIPT_DIR/data" --logfile "$SCRIPT_DIR/data/redis.log"

    # Wait for Redis
    for i in $(seq 1 10); do
      if "$REDIS_CLI" ping 2>/dev/null | grep -q PONG; then
        echo "  Redis is ready!"
        break
      fi
      sleep 1
    done

    if ! "$REDIS_CLI" ping 2>/dev/null | grep -q PONG; then
      echo "  Warning: Redis did not start. Check data/redis.log"
    fi
  else
    echo "  Warning: Could not get redis-server. Bot may fail."
  fi
fi

echo ""
echo "=== Starting Bot ==="
echo "  REDIS_URL = ${REDIS_URL:-not set in env}"
python main.py
