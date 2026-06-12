"""准实盘行情接入骨架：CTP-like / gateway-like 通用 adapter。

老板提到“可以通过服务器连接真实行情源测试”，但真实柜台（CTP / 公司内部
网关）不应写死在这里。本模块提供一个通用的 ``LiveGatewaySource`` 抽象：

* ``provider="mock"``（默认）—— 纯本地合成行情，不需要任何服务器，可用于
  测试和冒烟。
* ``provider="ctp"`` / ``"ctpx"`` / ``"internal"`` —— 真实 provider 的
  **占位接口**。本次不强依赖任何第三方 CTP 库；真实接入将在服务器环境中
  通过 ``data_engine/sources/providers/<provider>.py`` 的 connector 实现。

安全与懒加载约定
----------------
* **import 时不连接网络**：模块顶层只做纯导入。
* **连接动作全部在 ``stream()`` / ``warmup()`` 内部懒启动**。
* **账号密码不写进配置**：配置里只存环境变量名 ``password_env``，真正的密码
  在需要时才从环境变量读取，从不持久化、从不出现在对象属性里。
* 选择真实 provider 但配置不完整或依赖未安装时，抛出清晰错误。

输出统一为 :class:`data_engine.events.BarEvent`，与其它 data_engine 源一致。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from data_engine.adapters.bar_adapter import make_bar_event
from data_engine.events import BarEvent
from data_engine.time import ONE_SECOND_NS

# 已知的真实 provider 名称（占位）。mock 之外都尚未实现 connector。
_REAL_PROVIDERS = frozenset({"ctp", "ctpx", "internal"})


@dataclass
class LiveGatewayConfig:
    """实盘网关配置。

    注意：这里**只有** ``password_env``（环境变量名），没有明文密码字段。
    """

    provider: str = "mock"
    front_addr: str | None = None
    broker_id: str | None = None
    user_id: str | None = None
    password_env: str | None = None
    instruments: list[str] = field(default_factory=list)
    frequency: str = "tick"
    reconnect: bool = True
    heartbeat_seconds: int = 30

    # --- 以下是 mock provider 的本地测试旋钮，对真实 provider 无意义 ---
    mock_warmup_bars: int = 20
    mock_stream_bars: int = 20
    mock_delay_seconds: float = 0.0


class LiveGatewaySource:
    """通用实盘行情源。连接全部懒启动，import 与构造时都不触网。"""

    def __init__(self, config: LiveGatewayConfig) -> None:
        self._config = config
        # 不在这里建立任何连接、不读取密码。

    # ------------------------------------------------------------------ api

    def warmup(self) -> list[BarEvent]:
        """返回 warmup 历史。mock 直接合成；真实 provider 懒连接后拉取。"""
        if self._config.provider == "mock":
            return list(self._mock_bars(self._config.mock_warmup_bars, start_index=0))
        self._connect_real()  # 真实 provider：占位，必定抛清晰错误
        return []  # pragma: no cover - 真实 connector 尚未实现

    def stream(self) -> Iterator[BarEvent]:
        """返回实时事件流。**所有连接动作在此懒启动。**"""
        if self._config.provider == "mock":
            yield from self._mock_bars(
                self._config.mock_stream_bars,
                start_index=self._config.mock_warmup_bars,
                delay_seconds=self._config.mock_delay_seconds,
            )
            return
        # 真实 provider：在这里（而非 import / __init__）才连接。
        connector = self._connect_real()
        yield from connector.stream()  # pragma: no cover - 真实 connector 尚未实现

    # -------------------------------------------------------------- helpers

    def _resolve_password(self) -> str | None:
        """从环境变量读取密码；密码从不存进对象，也从不进配置文件。"""
        env = self._config.password_env
        if not env:
            return None
        return os.environ.get(env)

    def _connect_real(self) -> Any:
        """真实 provider 的连接占位。

        先校验配置完整性（缺啥说啥），再尝试加载对应 connector。当前不强依赖
        任何第三方 CTP 库，因此这里要么因配置不全报错，要么因 connector 未实现
        报错——两者都给出清晰、可操作的信息。
        """
        cfg = self._config
        provider = cfg.provider
        if provider == "mock":  # pragma: no cover - 调用方已分流
            raise AssertionError("mock provider 不应走真实连接路径")

        if provider not in _REAL_PROVIDERS:
            raise ValueError(
                f"未知 provider {provider!r}。支持: 'mock' 或 {sorted(_REAL_PROVIDERS)}"
            )

        # 1) 配置完整性检查。
        missing = [
            name
            for name, val in (
                ("front_addr", cfg.front_addr),
                ("broker_id", cfg.broker_id),
                ("user_id", cfg.user_id),
                ("password_env", cfg.password_env),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                f"provider={provider!r} 配置不完整，缺少: {missing}。"
                "（密码请通过环境变量提供，配置里只放 password_env 名称）"
            )
        if not cfg.instruments:
            raise ValueError(f"provider={provider!r} 需要至少一个 instrument")

        # 2) 密码必须能从环境变量取到。
        if self._resolve_password() is None:
            raise RuntimeError(
                f"环境变量 {cfg.password_env!r} 未设置或为空；无法连接 {provider!r}"
            )

        # 3) 加载 provider-specific connector（尚未实现 → 清晰 ImportError）。
        try:
            from importlib import import_module

            module = import_module(f"data_engine.sources.providers.{provider}")
        except ModuleNotFoundError as exc:
            raise ImportError(
                f"真实 provider {provider!r} 的 connector 尚未接入。"
                f"请在 data_engine/sources/providers/{provider}.py 实现，"
                "并安装对应柜台/网关依赖后再使用。"
            ) from exc
        return module.connect(cfg, self._resolve_password())  # pragma: no cover

    def _mock_bars(
        self, count: int, *, start_index: int, delay_seconds: float = 0.0
    ) -> Iterator[BarEvent]:
        """生成确定性的本地合成 bar，覆盖配置里的每个 instrument。"""
        import time

        instruments = self._config.instruments or ["MOCK.SIM"]
        for i in range(count):
            idx = start_index + i
            if delay_seconds > 0:
                time.sleep(delay_seconds)
            for inst in instruments:
                # 简单确定性游走：价格随 index 缓慢上行。
                close = 100.0 + idx * 0.5
                yield make_bar_event(
                    close=close,
                    open=close - 0.2,
                    high=close + 0.3,
                    low=close - 0.4,
                    volume=1000.0 + idx,
                    instrument_id=inst,
                    event_time_ns=idx * ONE_SECOND_NS,
                )


def load_live_gateway(
    data_config: dict[str, Any],
) -> tuple[list[BarEvent], Iterable[BarEvent]]:
    """从 config 的 ``data:`` 段构建 ``LiveGatewaySource``，返回 ``(warmup, live)``。

    config 示例::

        data:
          mode: live_gateway
          provider: mock
          instruments: [IH2303.CFFEX]
          frequency: tick
          password_env: CTP_PASSWORD   # 仅环境变量名，不放明文
    """
    config = LiveGatewayConfig(
        provider=str(data_config.get("provider", "mock")),
        front_addr=data_config.get("front_addr"),
        broker_id=data_config.get("broker_id"),
        user_id=data_config.get("user_id"),
        password_env=data_config.get("password_env"),
        instruments=list(data_config.get("instruments", [])),
        frequency=str(data_config.get("frequency", "tick")),
        reconnect=bool(data_config.get("reconnect", True)),
        heartbeat_seconds=int(data_config.get("heartbeat_seconds", 30)),
        mock_warmup_bars=int(data_config.get("warmup_bars", 20)),
        mock_stream_bars=int(data_config.get("live_bars", 20)),
        mock_delay_seconds=float(data_config.get("delay_seconds", 0.0)),
    )
    source = LiveGatewaySource(config)
    return source.warmup(), source.stream()
