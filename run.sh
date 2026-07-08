#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Starting Redis (user-space) ==="

# Check if Redis is already running
if redis-cli ping 2>/dev/null | grep -q PONG; then
  echo "  Redis is already running."
else
  # Check if redis-server is installed
  REDIS_BIN=""
  if command -v redis-server &>/dev/null; then
    REDIS_BIN="redis-server"
  else
    echo "  redis-server not found, installing via pip..."
    pip install redis-server 2>/dev/null
    REDIS_BIN=$(python -c "import redis_server; print(redis_server.REDIS_SERVER_PATH)" 2>/dev/null)
    if [ -z "$REDIS_BIN" ]; then
      echo "  Warning: Could not install redis-server. Bot may fail."
    fi
  fi

  # Start Redis as a background user process (no sudo needed)
  if [ -n "$REDIS_BIN" ]; then
    echo "  Starting Redis: $REDIS_BIN"
    "$REDIS_BIN" --daemonize yes --port 6379 --bind 127.0.0.1 \
      --maxmemory 256mb --maxmemory-policy allkeys-lru \
      --dir "$SCRIPT_DIR/data" --logfile "$SCRIPT_DIR/data/redis.log" 2>/dev/null || true
  fi

  # Wait for Redis to be ready
  echo "  Waiting for Redis..."
  for i in $(seq 1 15); do
    if redis-cli ping 2>/dev/null | grep -q PONG; then
      echo "  Redis is ready!"
      break
    fi
    sleep 1
  done

  if ! redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "  Warning: Redis did not start. Bot may fail if it needs the cache."
  fi
fi

echo ""
echo "=== Starting Bot ==="
python main.py
