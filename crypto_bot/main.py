"""Entry point: python -m crypto_bot.main"""

from __future__ import annotations

import asyncio
import sys

from crypto_bot.app import BotApp


def _install_uvloop() -> None:
    if sys.platform == "win32":
        return
    try:
        import uvloop

        uvloop.install()
    except ImportError:
        pass


def main() -> None:
    _install_uvloop()
    try:
        asyncio.run(BotApp().run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
