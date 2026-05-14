import re
from pathlib import Path


class InstrumentTypeInferencer:
    @staticmethod
    def infer_from_path_and_symbol(
        path: str | Path | None,
        symbol: str,
        hints: dict | None = None,
    ) -> dict:
        hints = hints or {}
        symbol_text = str(symbol or "").upper()
        path_text = str(path or "")
        path_lower = path_text.lower()
        combined_lower = f"{path_text} {symbol}".lower()

        hinted_type = hints.get("instrument_type")
        hinted_venue = hints.get("venue")
        if hinted_type:
            return {
                "instrument_type": hinted_type,
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": hints.get("asset_class"),
                "confidence": 1.0,
                "reason": "instrument_type provided explicitly in hints",
            }

        if ("option" in combined_lower or hints.get("option_kind") is not None) and (
            hints.get("strike_price") is not None or hints.get("expiry") is not None
        ):
            is_crypto = "crypto" in combined_lower or InstrumentTypeInferencer._looks_crypto(symbol_text)
            return {
                "instrument_type": "crypto_option" if is_crypto else "option_contract",
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": "crypto" if is_crypto else hints.get("asset_class"),
                "confidence": 0.85,
                "reason": "option path/symbol with strike or expiry hints",
            }

        if any(token in combined_lower for token in ["synthetic", "basket", "spread_formula"]):
            return {
                "instrument_type": "synthetic",
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": hints.get("asset_class"),
                "confidence": 0.55,
                "reason": "synthetic/basket token",
            }

        has_crypto_future_path = (
            "crypto" in path_lower
            and ("future" in path_lower or "futures" in path_lower)
        )
        has_expiry = bool(
            re.search(r"(20\d{4}|\d{6}|current_quarter|next_quarter)", combined_lower)
        )
        if has_crypto_future_path and has_expiry:
            return {
                "instrument_type": "crypto_future",
                "venue": str(hinted_venue or "BINANCE").upper(),
                "asset_class": "crypto",
                "confidence": 0.8,
                "reason": "crypto futures path with expiry-like token",
            }

        if (
            "crypto" in path_lower
            and "futures" in path_lower
            and ("binancecryptofutures" in path_lower or "binance" in path_lower)
            and InstrumentTypeInferencer._ends_with_stable_quote(symbol_text)
        ):
            return {
                "instrument_type": "crypto_perpetual",
                "venue": str(hinted_venue or "BINANCE").upper(),
                "asset_class": "crypto",
                "confidence": 0.9,
                "reason": "Binance crypto futures path with stable-quoted symbol",
            }

        if ("spot" in path_lower) and InstrumentTypeInferencer._looks_crypto(symbol_text):
            return {
                "instrument_type": "currency_pair",
                "venue": str(hinted_venue or "BINANCE").upper(),
                "asset_class": "crypto",
                "confidence": 0.75,
                "reason": "spot path with crypto-like symbol",
            }

        if "/" in symbol_text and "future" not in path_lower:
            return {
                "instrument_type": "currency_pair",
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": "fx_or_crypto",
                "confidence": 0.7,
                "reason": "slash-delimited symbol without futures path",
            }

        if any(token in combined_lower for token in ["equity", "stock", "shares"]):
            return {
                "instrument_type": "equity",
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": "equity",
                "confidence": 0.7,
                "reason": "equity-like path or symbol token",
            }

        if ("future" in combined_lower or "futures" in combined_lower) and "crypto" not in combined_lower:
            return {
                "instrument_type": "futures_contract",
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": hints.get("asset_class"),
                "confidence": 0.65,
                "reason": "non-crypto futures token",
            }

        if "cfd" in combined_lower:
            return {
                "instrument_type": "cfd",
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": hints.get("asset_class"),
                "confidence": 0.65,
                "reason": "CFD token",
            }

        if any(token in combined_lower for token in ["index", "spx", "nasdaq100", "ndx"]):
            return {
                "instrument_type": "index",
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": "index",
                "confidence": 0.65,
                "reason": "index token",
            }

        if any(token in combined_lower for token in ["commodity", "xau", "gold", "cl", "wti"]):
            return {
                "instrument_type": "commodity",
                "venue": str(hinted_venue or "UNKNOWN").upper(),
                "asset_class": "commodity",
                "confidence": 0.65,
                "reason": "commodity token",
            }

        return {
            "instrument_type": "unknown",
            "venue": str(hinted_venue or "UNKNOWN").upper(),
            "asset_class": hints.get("asset_class"),
            "confidence": 0.0,
            "reason": "no instrument inference rule matched",
        }

    @staticmethod
    def _ends_with_stable_quote(symbol: str) -> bool:
        return symbol.endswith(("USDT", "USDC", "USD"))

    @staticmethod
    def _looks_crypto(symbol: str) -> bool:
        normalized = symbol.replace("/", "")
        return normalized.endswith(("USDT", "USDC", "USD", "BTC", "ETH"))
