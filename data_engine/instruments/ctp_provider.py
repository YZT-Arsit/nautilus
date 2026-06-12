"""CTP / 国内柜台合约信息 provider —— 占位实现。

与 :class:`data_engine.instruments.ccxt_provider.CcxtInstrumentProvider` 对应，
但面向国内期货柜台（CTP 及兼容网关）。真实接入需要柜台 SDK 和登录信息，
因此本次只提供**占位**：满足 :class:`InstrumentProvider` 协议，但
``load_instruments()`` 抛出清晰的 :class:`NotImplementedError`，绝不返回假数据。

安全约定（与 live_gateway 一致）：账号/密码**只**通过环境变量名传入，绝不写进
配置文件、绝不存进对象属性。
"""
from __future__ import annotations

from dataclasses import dataclass

from data_engine.instruments.models import InstrumentInfo


@dataclass
class CtpInstrumentProviderConfig:
    """CTP provider 配置（不含明文密码）。"""

    front_addr: str | None = None
    broker_id: str | None = None
    user_id: str | None = None
    password_env: str | None = None  # 仅环境变量名


class CtpInstrumentProvider:
    """占位 provider：接口已定义，真实拉取尚未实现。"""

    def __init__(self, config: CtpInstrumentProviderConfig | None = None) -> None:
        self._config = config or CtpInstrumentProviderConfig()

    def load_instruments(self) -> list[InstrumentInfo]:
        raise NotImplementedError(
            "CtpInstrumentProvider 尚未接入真实柜台。需要 CTP SDK 与登录信息后，"
            "在此实现合约查询并归一化为 InstrumentInfo。"
            "（密码请通过环境变量 password_env 提供，不要写进配置。）"
            "在此之前，离线/测试请用 StaticInstrumentProvider。"
        )


__all__ = ["CtpInstrumentProvider", "CtpInstrumentProviderConfig"]
