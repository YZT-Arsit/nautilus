"""
Download and filter ccxt market metadata.

This module is responsible for:
- Creating a ccxt exchange instance with correct credentials and rate-limit settings.
- Calling load_markets() and caching the result in-process.
- Filtering markets by the configured symbols and market_type.
- Inferring the semantic market type (spot / swap_linear / swap_inverse / future).
- Optionally serialising the raw market dict to JSON for reproducibility.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from nautilus_ext.ccxt.ccxt_config import CcxtDataConfig

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)


class CcxtMarketConnector:
    """Thin wrapper around a ccxt exchange that downloads and caches market data."""

    def __init__(self, config: CcxtDataConfig) -> None:
        self.config = config
        self._exchange = None
        self._markets: dict | None = None

    # ------------------------------------------------------------------
    # Exchange lifecycle
    # ------------------------------------------------------------------

    @property
    def exchange(self):
        if self._exchange is None:
            self._exchange = self._create_exchange()
        return self._exchange

    def _create_exchange(self):
        try:
            import ccxt
        except ImportError as exc:
            raise ImportError(
                "ccxt is required for CcxtBarDataConnector. "
                "Install it with:  pip install ccxt"
            ) from exc

        exchange_cls = getattr(ccxt, self.config.exchange_id, None)
        if exchange_cls is None:
            raise ValueError(
                f"Unknown ccxt exchange id {self.config.exchange_id!r}. "
                f"Run `python -c 'import ccxt; print(ccxt.exchanges)'` to list available exchanges."
            )

        params: dict = {"enableRateLimit": self.config.enable_rate_limit}

        api_key = self.config.resolved_api_key()
        secret = self.config.resolved_secret()
        password = self.config.resolved_password()

        if api_key:
            params["apiKey"] = api_key
        if secret:
            params["secret"] = secret
        if password:
            params["password"] = password

        if self.config.params:
            params.update(self.config.params)

        exchange = exchange_cls(params)

        if self.config.sandbox:
            try:
                exchange.set_sandbox_mode(True)
            except Exception as exc:
                log.warning(
                    "sandbox=True requested but exchange %r does not support sandbox mode: %s",
                    self.config.exchange_id, exc,
                )

        return exchange

    # ------------------------------------------------------------------
    # Market loading
    # ------------------------------------------------------------------

    def load_markets(self, reload: bool = False) -> dict:
        """Download and cache markets from the exchange.

        Returns a dict keyed by ccxt symbol string, e.g. "BTC/USDT".
        The result is cached in-process; pass reload=True to force a refresh.
        """
        if self._markets is not None and not reload:
            return self._markets
        log.info("Loading markets from %r ...", self.config.exchange_id)
        self._markets = self.exchange.load_markets()
        log.info("Loaded %d markets from %r.", len(self._markets), self.config.exchange_id)
        return self._markets

    def get_market(self, symbol: str) -> dict:
        """Return the ccxt market dict for a single symbol.

        Raises ValueError with a clear message if the symbol is not found.
        """
        markets = self.load_markets()
        if symbol not in markets:
            sample = sorted(markets.keys())[:10]
            raise ValueError(
                f"Symbol {symbol!r} not found in {self.config.exchange_id!r} markets. "
                f"First 10 available: {sample}"
            )
        return markets[symbol]

    def list_markets(self) -> list[dict]:
        """Return market dicts for all configured symbols.

        If config.symbols is non-empty, only those symbols are returned
        (in the order given).  Missing symbols are skipped with a warning.
        """
        markets = self.load_markets()
        symbols = self.config.symbols
        if not symbols:
            return list(markets.values())

        result = []
        for sym in symbols:
            if sym in markets:
                result.append(markets[sym])
            else:
                log.warning("Symbol %r not found in %r markets; skipping.", sym, self.config.exchange_id)
        return result

    # ------------------------------------------------------------------
    # Market type inference
    # ------------------------------------------------------------------

    def infer_market_type(self, market: dict) -> str:
        """Return one of: "spot", "swap_linear", "swap_inverse", "future_linear",
        "future_inverse", "option", "unknown".

        Priority:
        1. config.instrument_kind override (if set)
        2. ccxt market boolean fields (spot / swap / future / option)
        3. linear / inverse sub-classification
        """
        kind_override = self.config.instrument_kind
        if kind_override:
            override_map = {
                "spot": "spot",
                "perpetual": "swap_linear",
                "swap": "swap_linear",
                "future": "future_linear",
            }
            return override_map.get(kind_override.lower(), "swap_linear")

        if market.get("spot"):
            return "spot"
        if market.get("swap") or market.get("type") == "swap":
            if market.get("inverse"):
                return "swap_inverse"
            return "swap_linear"
        if market.get("future") or market.get("futures") or market.get("type") == "future":
            if market.get("inverse"):
                return "future_inverse"
            return "future_linear"
        if market.get("option"):
            return "option"

        # Fallback: try to read the 'type' string field directly
        mtype = str(market.get("type", "")).lower()
        if mtype == "spot":
            return "spot"
        if mtype in ("swap", "perpetual"):
            return "swap_linear"
        if mtype == "future":
            return "future_linear"

        log.warning(
            "Cannot infer market type for symbol %r; defaulting to 'swap_linear'. "
            "Set config.instrument_kind to override.",
            market.get("symbol"),
        )
        return "swap_linear"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_markets(self, path: str | Path) -> None:
        """Serialise the loaded markets dict to a JSON file."""
        markets = self.load_markets()
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as fh:
            json.dump(markets, fh, indent=2, default=str)
        log.info("Saved %d markets to %s", len(markets), dest)

    # ------------------------------------------------------------------
    # Precision mode
    # ------------------------------------------------------------------

    @property
    def precision_mode(self) -> int:
        """Return the ccxt precisionMode constant for this exchange.

        Returns 2 (DECIMAL_PLACES) by default if the attribute is not set.
        """
        return getattr(self.exchange, "precisionMode", 2)
