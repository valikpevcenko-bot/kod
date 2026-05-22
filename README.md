# 📊 Crypto Price Telegram Bot

Telegram-бот на **Python + CCXT + aiogram 3.x**.  
По команде `/get BTCUSDT` показывает **Spot** и **Futures** цены, **изменение за 24ч** и **ссылки** на торговые пары.

## 🏦 Поддерживаемые биржи

| Биржа | Spot | Futures |
|--------|------|---------|
| Binance | ✅ | ✅ |
| Bybit | ✅ | ✅ |
| OKX | ✅ | ✅ |
| Gate.io | ✅ | ✅ |
| MEXC | ✅ | ✅ |
| Bitget | ✅ | ✅ |
| KuCoin | ✅ | ✅ |
| BingX | ✅ | ✅ |
| HTX | ✅ | ✅ |
| Hyperliquid | — | ✅ |

---

## 🚀 Пошаговый запуск (для новичка)

### Шаг 1. Установи Python

Нужен **Python 3.10+**.

Проверка в терминале:

```bash
python3 --version
```

### Шаг 2. Скачай проект и открой папку

```bash
cd /Users/valentynpevchenko/Documents/crypto-telegram-bot
```

### Шаг 3. Создай виртуальное окружение

```bash
python3 -m venv .venv
```

Активация:

- **macOS / Linux:**
  ```bash
  source .venv/bin/activate
  ```
- **Windows:**
  ```cmd
  .venv\Scripts\activate
  ```

После активации в начале строки терминала появится `(.venv)`.

### Шаг 4. Установи зависимости

```bash
pip install -r requirements.txt
```

### Шаг 5. Создай бота в Telegram

1. Открой [@BotFather](https://t.me/BotFather)
2. Отправь `/newbot`
3. Придумай имя и username
4. Скопируй **токен** (выглядит как `123456789:AA...`)

### Шаг 6. Настрой `.env`

```bash
cp .env.example .env
```

Открой файл `.env` в любом редакторе и вставь токен:

```
BOT_TOKEN=твой_токен_от_BotFather
```

### Шаг 7. Запусти бота

```bash
python bot.py
```

В терминале должно появиться: `Бот запущен. Ожидаю сообщения…`

### Шаг 8. Проверь в Telegram

1. Найди своего бота по username
2. Нажми **Start**
3. Отправь:
   ```
   /get BTCUSDT
   /get ETHUSDT
   /get SOLUSDT
   ```

---

## 📁 Структура проекта

```
crypto-telegram-bot/
├── bot.py           # Точка входа, команды Telegram
├── config.py        # Загрузка BOT_TOKEN из .env
├── ticker_parser.py # Разбор BTCUSDT → BTC + USDT
├── fetcher.py       # Запросы к биржам через CCXT (async)
├── formatter.py     # Красивый Markdown-ответ
├── links.py         # URL торговых пар
├── models.py        # Структуры данных
├── requirements.txt
├── .env.example
└── README.md
```

---

## 💬 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и справка |
| `/help` | То же, что `/start` |
| `/get TICKER` | Цены по всем биржам |

**Примеры тикеров:** `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `BTC/USDT`, `SOL` (по умолчанию к USDT).

---

## ⚙️ Дополнительные настройки

В `.env` можно задать котируемую валюту по умолчанию (если ввели только базу):

```
DEFAULT_QUOTE=USDT
```

---

## ❓ Частые проблемы

**`❌ Не найден BOT_TOKEN`**  
→ Создай файл `.env` и добавь `BOT_TOKEN=...`

**Бот не отвечает**  
→ Убедись, что `python bot.py` запущен и нет ошибок в терминале.

**«Тикер не найден»**  
→ Проверь, что монета торгуется к USDT на биржах (например `PEPEUSDT`).

**Долгий ответ**  
→ Бот опрашивает ~10 бирж; первый запрос может занять 5–15 секунд.

---

## 📜 Лицензия

Свободное использование в личных целях.
