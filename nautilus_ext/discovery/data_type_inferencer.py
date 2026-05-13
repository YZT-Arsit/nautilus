class DataTypeInferencer:
    @staticmethod
    def infer_from_columns(columns: list[str]) -> str:
        normalized = {column.lower() for column in columns}

        has_bar_fields = (
            DataTypeInferencer._contains_any(normalized, {"open", "open_price", "open_px"})
            and DataTypeInferencer._contains_any(normalized, {"high", "high_price", "high_px"})
            and DataTypeInferencer._contains_any(normalized, {"low", "low_price", "low_px"})
            and DataTypeInferencer._contains_any(normalized, {"close", "close_price", "close_px"})
        )
        if has_bar_fields:
            return "bar"

        has_trade_fields = {"price", "qty"}.issubset(normalized) and (
            "trade_id" in normalized or "agg_trade_id" in normalized
        )
        if has_trade_fields:
            return "trade_tick"

        has_bid_ask = {"bid", "ask"}.issubset(normalized) or {
            "bid_price",
            "ask_price",
        }.issubset(normalized)
        if has_bid_ask:
            return "quote_tick"

        return "unknown"

    @staticmethod
    def _contains_any(columns: set[str], candidates: set[str]) -> bool:
        return bool(columns.intersection(candidates))
