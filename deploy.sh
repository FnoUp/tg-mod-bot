#!/usr/bin/env bash
# Полностью автоматический деплой: устанавливает Docker при необходимости,
# создаёт .env и поднимает бота через docker compose.
#
# Использование:
#   ./deploy.sh                       — если .env уже настроен
#   ./deploy.sh <BOT_TOKEN>           — создаст .env с токеном
#   ./deploy.sh <BOT_TOKEN> <ADMIN_IDS>
set -euo pipefail

cd "$(dirname "$0")"

BOT_TOKEN_ARG="${1:-}"
ADMIN_IDS_ARG="${2:-}"

if ! command -v docker >/dev/null 2>&1; then
    echo "Docker не найден — устанавливаю..."
    curl -fsSL https://get.docker.com | sh
fi

if ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose plugin не найден — устанавливаю..."
    apt-get update -y && apt-get install -y docker-compose-plugin
fi

if [ ! -f .env ]; then
    cp .env.example .env
    if [ -n "$BOT_TOKEN_ARG" ]; then
        sed -i "s|^BOT_TOKEN=.*|BOT_TOKEN=${BOT_TOKEN_ARG}|" .env
    fi
    if [ -n "$ADMIN_IDS_ARG" ]; then
        sed -i "s|^ADMIN_IDS=.*|ADMIN_IDS=${ADMIN_IDS_ARG}|" .env
    fi
fi

if ! grep -q "^BOT_TOKEN=.\+" .env; then
    echo "BOT_TOKEN не задан."
    echo "Впиши токен в .env или перезапусти: ./deploy.sh <token> [admin_id1,admin_id2]"
    exit 1
fi

mkdir -p data
docker compose up -d --build

echo "Готово. Логи: docker compose logs -f"
