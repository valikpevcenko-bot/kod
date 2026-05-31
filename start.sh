#!/bin/bash
# Запуск Crypto Telegram Bot (macOS / Linux)
# Использование: bash start.sh

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "❌ Нет файла .env"
  exit 1
fi

if grep -qE '^BOT_TOKEN=(|ВСТАВЬ|your_token)' .env 2>/dev/null; then
  echo "❌ В .env не указан BOT_TOKEN"
  echo "   1. Telegram → @BotFather → /token"
  echo "   2. Вставь в .env: BOT_TOKEN=123456789:ABC..."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "📦 Создаю виртуальное окружение..."
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
fi

echo "🚀 Запуск bot.py (Ctrl+C — остановка)"
echo ""
exec .venv/bin/python bot.py
