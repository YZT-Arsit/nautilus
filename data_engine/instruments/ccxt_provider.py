"""CCXT 合约信息 provider。

通过 CCXT 下载加密交易所的 market metadata（现货 / 永续 / 交割 / 期权的符号、
合约乘数、精度、最小下单量、tick size、到期、是否活跃等），归一化为
:class:`~data_engine.instruments.models.InstrumentInfo`。

约定
----
* ``ccxt`` **懒加载**：只在 :meth:`CcxtInstrumentProvider.load_instruments`
  内部 import，模块 import 期不依赖 ccxt、不连网络。
* 未安装 ccxt 时抛出清晰 :class:`ImportError`，提示安装方式。
* 公共 market metadata **不需要 API key**。
* 归一化函数 :func:`instrument_from_ccxt_market` 是纯函数，可脱离网络单测。

国内期货 / CTP / 公司柜台**不要**走 CCXT；它们通过独立的 provider 接口接入。
"""
from __future__ import annotations

from typing import Any

from data_engine.instruments.models import InstrumentInfo

# CCXT 的 type 字段可能值。
_MARKET_TYPES = ("spot", "swap", "future", "option")


def _market_type(market: dict[str, Any]) -> str:
    """从 ccxt market dict 推断统一的 market_type。"""
    for t in _MARKET_TYPES:
        if market.get(t):
            return t
    return str(market.get("type") or "unknown")


def _split_precision(value: Any) -> tuple[int | None, float | None]:
    """把 ccxt 的 precision 值拆成 (整数精度位数, tick/step)。

    ccxt 有两种 precisionMode：
    * DECIMAL_PLACES —— precision 是整数“小数位数”，如 ``2``。
    * TICK_SIZE      —— precision 是实际步长（小数），如 ``0.01``。

    用整数性区分：整数 → 精度位数；非整数 → tick/step。
    """
    if value is None:
        return None, None
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return None, None
    if fval == int(fval) and fval >= 1:
        return int(fval), None
    if fval == int(fval) and fval == 0:
        return 0, None
    return None, fval


def _limit(market: dict[str, Any], kind: str, bound: str) -> float | None:
    """安全读取 ``market['limits'][kind][bound]``。"""
    limits = market.get("limits") or {}
    section = limits.get(kind) or {}
    val = section.get(bound)
    return None if val is None else float(val)


def instrument_from_ccxt_market(
    market: dict[str, Any], exchange_id: str
) -> InstrumentInfo:
    """把一个 ccxt market dict 归一化为 :class:`InstrumentInfo`（纯函数，无网络）。"""
    symbol = str(market.get("symbol") or market.get("id") or "")
    precision = market.get("precision") or {}
    price_prec, price_tick = _split_precision(precision.get("price"))
    amount_prec, amount_step = _split_precision(precision.get("amount"))

    contract_size = market.get("contractSize")
    expiry = market.get("expiry")

    return InstrumentInfo(
        instrument_id=f"{symbol}.{exchange_id}",
        exchange=exchange_id,
        symbol=symbol,
        market_type=_market_type(market),
        base=market.get("base"),
        quote=market.get("quote"),
        settle=market.get("settle"),
        contract_size=None if contract_size is None else float(contract_size),
        price_precision=price_prec,
        amount_precision=amount_prec,
        price_tick=price_tick,
        amount_step=amount_step,
        min_amount=_limit(market, "amount", "min"),
        min_notional=_limit(market, "cost", "min"),
        expiry=None if expiry is None else int(expiry),
        active=market.get("active"),
        raw=dict(market),
    )


class CcxtInstrumentProvider:
    """从 CCXT 拉取并归一化合约信息。

    Parameters
    ----------
    exchange_id : ccxt 交易所 id，如 ``"binance"``。
    market_type : 若给定，仅保留该类型（``spot`` / ``swap`` / ``future`` /
        ``option``）。
    symbols : 若给定，仅保留这些 ccxt 统一符号。
    enable_rate_limit : 传给 ccxt 交易所的限频开关。
    """

    def __init__(
        self,
        exchange_id: str,
        *,
        market_type: str | None = None,
        symbols: list[str] | None = None,
        enable_rate_limit: bool = True,
    ) -> None:
        self.exchange_id = exchange_id
        self.market_type = market_type
        self.symbols = set(symbols) if symbols else None
        self.enable_rate_limit = enable_rate_limit

    def load_instruments(self) -> list[InstrumentInfo]:
        """下载并归一化合约信息。``ccxt`` 在此懒加载。"""
        try:
            import ccxt  # noqa: PLC0415 - 懒加载，import 期不依赖 ccxt
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise ImportError(
                "未安装 ccxt，无法下载交易所合约信息。"
                "请先安装：pip install ccxt"
            ) from exc

        try:
            exchange_cls = getattr(ccxt, self.exchange_id)
        except AttributeError as exc:
            raise ValueError(
                f"ccxt 不支持交易所 {self.exchange_id!r}"
            ) from exc

        exchange = exchange_cls({"enableRateLimit": self.enable_rate_limit})
        markets = exchange.load_markets()
        return self._normalize_markets(markets)

    def _normalize_markets(
        self, markets: dict[str, dict[str, Any]]
    ) -> list[InstrumentInfo]:
        """把 ``load_markets()`` 的结果归一化 + 过滤（可脱离网络单测）。"""
        out: list[InstrumentInfo] = []
        for sym, market in markets.items():
            if self.symbols is not None and sym not in self.symbols:
                continue
            info = instrument_from_ccxt_market(market, self.exchange_id)
            if self.market_type is not None and info.market_type != self.market_type:
                continue
            out.append(info)
        return out
