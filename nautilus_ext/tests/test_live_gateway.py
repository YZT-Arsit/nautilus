"""准实盘行情接入骨架 live_gateway 测试。

全部本地运行：不连真实服务器、不依赖真实 CTP 库、import 不触网。
"""
from __future__ import annotations

import dataclasses

import pytest

from data_engine import load_events
from data_engine.sources.live_gateway import (
    LiveGatewayConfig,
    LiveGatewaySource,
    load_live_gateway,
)


def test_mock_provider_generates_events() -> None:
    cfg = LiveGatewayConfig(
        provider="mock",
        instruments=["IH2303.CFFEX"],
        mock_warmup_bars=5,
        mock_stream_bars=4,
    )
    source = LiveGatewaySource(cfg)
    warmup = source.warmup()
    live = list(source.stream())
    assert len(warmup) == 5
    assert len(live) == 4
    assert all(e.instrument_id == "IH2303.CFFEX" for e in warmup + live)
    # warmup 与 live 的时间戳不重叠（live 接在 warmup 之后）。
    assert live[0].event_time_ns > warmup[-1].event_time_ns


def test_mock_provider_multiple_instruments() -> None:
    cfg = LiveGatewayConfig(
        provider="mock",
        instruments=["A.X", "B.X"],
        mock_warmup_bars=3,
        mock_stream_bars=0,
    )
    warmup = LiveGatewaySource(cfg).warmup()
    # 每个 index 给每个 instrument 一条 → 3 * 2 = 6。
    assert len(warmup) == 6
    assert {e.instrument_id for e in warmup} == {"A.X", "B.X"}


def test_loader_mode_live_gateway() -> None:
    warmup, live = load_events(
        {
            "mode": "live_gateway",
            "provider": "mock",
            "instruments": ["MOCK.SIM"],
            "warmup_bars": 6,
            "live_bars": 3,
        }
    )
    assert len(warmup) == 6
    assert len(list(live)) == 3


def test_ctp_provider_incomplete_config_raises_clear_error() -> None:
    cfg = LiveGatewayConfig(provider="ctp", instruments=["IF2306.CFFEX"])
    source = LiveGatewaySource(cfg)
    # 构造不触发连接；只有 stream()/warmup() 才会连接并因配置不全报错。
    with pytest.raises(ValueError, match="配置不完整"):
        list(source.stream())


def test_unknown_provider_raises() -> None:
    cfg = LiveGatewayConfig(provider="definitely_not_a_provider", instruments=["X"])
    with pytest.raises(ValueError, match="未知 provider"):
        list(LiveGatewaySource(cfg).stream())


def test_construction_does_not_connect() -> None:
    # 即使配置完全为空、provider 是真实柜台，构造也不应抛错或连接。
    cfg = LiveGatewayConfig(provider="ctp")
    source = LiveGatewaySource(cfg)  # 不应抛异常
    assert isinstance(source, LiveGatewaySource)


def test_import_does_not_open_network(monkeypatch) -> None:
    """重新导入 live_gateway 模块时，即使禁掉 socket 连接也不应报错。

    证明所有连接动作都不在 import 期发生（而是懒启动于 stream()/warmup()）。
    """
    import importlib
    import socket

    def _no_connect(*_a, **_k):  # pragma: no cover - 不应被 import 触发
        raise AssertionError("import 期不应建立网络连接")

    monkeypatch.setattr(socket.socket, "connect", _no_connect)
    import data_engine.sources.live_gateway as lg

    importlib.reload(lg)  # 不应抛 AssertionError
    importlib.reload(lg)
    assert hasattr(lg, "LiveGatewaySource")


def test_config_has_no_plaintext_password_field() -> None:
    field_names = {f.name for f in dataclasses.fields(LiveGatewayConfig)}
    assert "password" not in field_names
    # 只允许放环境变量名。
    assert "password_env" in field_names


def test_password_read_lazily_from_env(monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CTP_PWD", "s3cret")
    cfg = LiveGatewayConfig(
        provider="ctp",
        front_addr="tcp://1.2.3.4:5000",
        broker_id="9999",
        user_id="u1",
        password_env="FAKE_CTP_PWD",
        instruments=["IF2306.CFFEX"],
    )
    source = LiveGatewaySource(cfg)
    # 密码不存在对象属性里（不持久化明文）。
    assert "s3cret" not in repr(vars(source))
    assert source._resolve_password() == "s3cret"
    # connector 尚未实现 → 清晰 ImportError（配置/密码都齐全才走到这一步）。
    with pytest.raises(ImportError, match="尚未接入"):
        list(source.stream())
