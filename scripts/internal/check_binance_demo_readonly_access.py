#!/usr/bin/env python3
"""Verify Binance Demo futures account access using signed GET requests only."""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


BASE = "https://demo-fapi.binance.com"
OUTPUT = Path(
    "/Users/Hoshino/Documents/nautilus/outputs/baseline_evaluation/"
    "demo_paper_ab_isolation_resolution/demo_readonly_access_validation.json"
)


def request_json(path: str, *, api_key: str | None = None, secret: str | None = None,
                 params: dict[str, object] | None = None) -> object:
    values = dict(params or {})
    headers = {"User-Agent": "nautilus-demo-readonly-preflight/1.0"}
    if api_key is not None:
        values.setdefault("timestamp", int(time.time() * 1000))
        values.setdefault("recvWindow", 5_000)
        query = urllib.parse.urlencode(values)
        signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        query = f"{query}&signature={signature}"
        headers["X-MBX-APIKEY"] = api_key
    else:
        query = urllib.parse.urlencode(values)
    url = f"{BASE}{path}" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=20, context=ssl.create_default_context()) as response:
        return json.load(response)


def main() -> int:
    api_key = getpass.getpass("Demo API key (input hidden): ").strip()
    secret = getpass.getpass("Demo API secret (input hidden): ").strip()
    if not api_key or not secret:
        raise SystemExit("credentials missing")

    result: dict[str, object] = {
        "environment": "BINANCE_DEMO",
        "endpoint": BASE,
        "tls_verification": "ENABLED",
        "request_mode": "SIGNED_GET_ONLY",
        "production_credentials_used": False,
        "production_exchange_orders": 0,
        "demo_exchange_orders_submitted": 0,
        "credential_persisted": False,
        "account_key_hash": hashlib.sha256(api_key.encode()).hexdigest()[:16],
    }
    try:
        server_time = request_json("/fapi/v1/time")
        offset = int(server_time["serverTime"]) - int(time.time() * 1000)

        def signed(path: str, params: dict[str, object] | None = None) -> object:
            values = dict(params or {})
            values["timestamp"] = int(time.time() * 1000) + offset
            return request_json(path, api_key=api_key, secret=secret, params=values)

        account = signed("/fapi/v2/account")
        balances = signed("/fapi/v2/balance")
        positions = signed("/fapi/v2/positionRisk", {"symbol": "BTCUSDT"})
        open_orders = signed("/fapi/v1/openOrders", {"symbol": "BTCUSDT"})
        exchange_info = request_json("/fapi/v1/exchangeInfo")
        btc = next(item for item in exchange_info["symbols"] if item["symbol"] == "BTCUSDT")
        nonzero_assets = [
            {
                "asset": row["asset"],
                "balance": row["balance"],
                "available_balance": row["availableBalance"],
            }
            for row in balances
            if float(row["balance"]) != 0.0 or float(row["availableBalance"]) != 0.0
        ]
        btc_positions = [
            {
                "symbol": row["symbol"],
                "position_side": row.get("positionSide"),
                "position_amt": row["positionAmt"],
                "entry_price": row["entryPrice"],
                "unrealized_profit": row["unRealizedProfit"],
            }
            for row in positions
        ]
        result.update({
            "status": "PASSED",
            "authenticated": True,
            "can_trade_flag": bool(account.get("canTrade")),
            "can_deposit_flag": bool(account.get("canDeposit")),
            "can_withdraw_flag": bool(account.get("canWithdraw")),
            "fee_tier": account.get("feeTier"),
            "total_wallet_balance": account.get("totalWalletBalance"),
            "available_balance": account.get("availableBalance"),
            "nonzero_assets": nonzero_assets,
            "btcusdt_positions": btc_positions,
            "btcusdt_open_order_count": len(open_orders),
            "btcusdt_status": btc.get("status"),
            "btcusdt_order_types": btc.get("orderTypes"),
            "read_endpoints": [
                "/fapi/v1/time", "/fapi/v2/account", "/fapi/v2/balance",
                "/fapi/v2/positionRisk?symbol=BTCUSDT",
                "/fapi/v1/openOrders?symbol=BTCUSDT", "/fapi/v1/exchangeInfo",
            ],
        })
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            payload = {"message": str(exc)}
        result.update({
            "status": "BLOCKED",
            "authenticated": False,
            "http_status": exc.code,
            "binance_error_code": payload.get("code"),
            "binance_error_message": payload.get("msg", payload.get("message")),
        })
    except Exception as exc:
        result.update({
            "status": "BLOCKED",
            "authenticated": False,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
        })
    finally:
        api_key = ""
        secret = ""

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result.get("status") == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
