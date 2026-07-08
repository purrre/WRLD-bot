#!/bin/bash
set -e

echo "=== Redis Setup Script ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash setup-redis.sh"
  exit 1
fi

# Detect OS
if [ -f /etc/debian_version ]; then
  OS="debian"
elif [ -f /etc/redhat-release ]; then
  OS="redhat"
elif [ -f /etc/arch-release ]; then
  OS="arch"
else
  OS="unknown"
fi

# Install Redis
echo ">>> Installing Redis..."
case $OS in
  debian)
    apt-get update -y
    apt-get install -y redis-server
    ;;
  redhat)
    yum install -y epel-release
    yum install -y redis
    ;;
  arch)
    pacman -Sy --noconfirm redis
    ;;
  *)
    echo "Unsupported OS. Install Redis manually."
    exit 1
    ;;
esac

# Configure Redis
echo ">>> Configuring Redis..."
REDIS_CONF="/etc/redis/redis.conf"
if [ -f "$REDIS_CONF" ]; then
  # Bind to all interfaces (adjust as needed)
  sed -i 's/^bind 127.0.0.1/bind 0.0.0.0/' "$REDIS_CONF"
  # Run as daemon
  sed -i 's/^supervised no/supervised systemd/' "$REDIS_CONF"
  # Disable protected mode (only if behind firewall)
  sed -i 's/^protected-mode yes/protected-mode no/' "$REDIS_CONF"
  # Set max memory (256mb) with LRU eviction
  sed -i 's/^# maxmemory <bytes>/maxmemory 256mb/' "$REDIS_CONF"
  sed -i 's/^# maxmemory-policy noeviction/maxmemory-policy allkeys-lru/' "$REDIS_CONF"
  echo "  Configured: $REDIS_CONF"
else
  echo "  Warning: $REDIS_CONF not found, using defaults"
fi

# Enable and start Redis service
echo ">>> Starting Redis..."
case $OS in
  debian|arch)
    systemctl enable redis-server
    systemctl restart redis-server
    ;;
  redhat)
    systemctl enable redis
    systemctl restart redis
    ;;
esac

# Wait for Redis to be ready
echo ">>> Waiting for Redis to accept connections..."
for i in $(seq 1 10); do
  if redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "  Redis is ready!"
    break
  fi
  sleep 1
done

# Verify
echo ""
echo "=== Redis Status ==="
redis-cli ping
redis-cli info server | grep -E "redis_version|uptime_in_seconds|tcp_port"
redis-cli info memory | grep -E "used_memory_human|maxmemory_human|maxmemory_policy"

echo ""
echo "=== Redis is running on port 6379 ==="
echo "To start manually:  redis-server --daemonize yes"
echo "To stop:            redis-cli shutdown"
echo "To check status:    redis-cli ping"
echo "To flush:           redis-cli flushall"

# ---- Bot ----
echo ""
echo "=== Starting Bot ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Install Python dependencies
if [ -f requirements.txt ]; then
  echo ">>> Installing Python dependencies..."
  pip install -r requirements.txt -q
fi

# Start the bot
echo ">>> Launching bot..."
python main.py
