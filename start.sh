#!/bin/bash
# Запуск Telegram-бота (macOS)
# Использование: ./start.sh   или   bash start.sh

cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "❌ Нет файла .env"
  echo "   Выполни: cp .env.example .env"
  echo "   и вставь BOT_TOKEN от @BotFather"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "📦 Создаю виртуальное окружение..."
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi

echo "🚀 Запускаю бота... (остановка: Ctrl+C)"
echo ""
.venv/bin/python bot.py
