#!/bin/bash
# Запуск Crypto Telegram Bot (macOS / Linux)
# Использование: bash start.sh

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "❌ Нет файла .env — скопируй: cp .env.example .env"
  exit 1
fi

if grep -qE '^BOT_TOKEN=(|ВСТАВЬ|your_token)' .env 2>/dev/null; then
  echo "❌ В .env не указан BOT_TOKEN"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "📦 Создаю виртуальное окружение..."
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi

echo "🚀 Запуск crypto_bot (Ctrl+C — остановка)"
echo "   Не запускай info-bot/bot.py — это старая копія."
echo "   Wait for «✅ Bot ready» — then send /get in Telegram."
echo ""
exec .venv/bin/python -m crypto_bot.main
