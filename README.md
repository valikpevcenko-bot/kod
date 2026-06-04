# Crypto Telegram Bot

Telegram-бот на **Python 3.12+, aiogram 3.x, httpx, pydantic-settings, structlog**.  
Команда `/get <тикер>` — spot, perpetual futures, funding, D/W, контракты с 11 бирж.

## Биржи (порядок в отчёте)

Binance → Bybit → Gate.io → MEXC → Bitget → OKX → KuCoin → BingX → AsterDEX → Hyperliquid

## Структура (Feature-Sliced)

```
crypto_bot/
├── main.py              # Точка входа
├── app.py               # Dispatcher, lifecycle
├── config/settings.py   # .env (pydantic-settings)
├── core/                # http, retry, guards, logging
├── models/              # Pydantic domain models
├── domain/              # exchanges registry, links, ticker parser
├── clients/
│   ├── coinmarketcap.py
│   └── exchanges/market.py
├── services/
│   ├── market_service.py
│   ├── wallet.py
│   ├── contracts.py
│   ├── formatter.py
│   └── report_cache.py
└── handlers/            # /start, /get, errors
```

## Быстрый старт

```bash
cp .env.example .env
# Вставь BOT_TOKEN от @BotFather

bash start.sh
# или: python3 -m venv .venv && source .venv/bin/activate
# pip install -r requirements.txt && python -m crypto_bot.main
```

В Telegram: `/get sol`, `/get btc`, `/get ethusdt`

## Docker

```bash
docker build -t crypto-bot .
docker run --env-file .env crypto-bot
```

## systemd

```bash
sudo cp deploy/crypto-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crypto-bot
```

## Опционально

- `CMC_API_KEY` — контракты и disambiguation через CoinMarketCap Pro
- API-ключи бирж — D/W для Bybit, OKX, BingX (read-only; OKX: IP whitelist на okx.com)

## Тесты

```bash
python test_bot_guards.py
```
