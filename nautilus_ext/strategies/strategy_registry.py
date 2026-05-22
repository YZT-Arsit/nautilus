from __future__ import annotations

from typing import Callable


_SIGNAL_ENGINE_FACTORIES: dict[str, Callable[[dict], object]] = {}


def register_signal_engine(kind: str, factory: Callable[[dict], object]) -> None:
    normalized = _normalize_kind(kind)
    if not callable(factory):
        raise TypeError("factory must be callable.")
    _SIGNAL_ENGINE_FACTORIES[normalized] = factory


def build_signal_engine(kind: str, params: dict) -> object:
    normalized = _normalize_kind(kind)
    factory = _SIGNAL_ENGINE_FACTORIES.get(normalized)
    if factory is None:
        available = available_signal_engines()
        raise ValueError(
            f"Unknown strategy_kind={kind!r}. "
            f"Available signal engines: {available}.",
        )
    return factory(dict(params))


def available_signal_engines() -> list[str]:
    return sorted(_SIGNAL_ENGINE_FACTORIES)


def _normalize_kind(kind: str) -> str:
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("kind must be a non-empty string.")
    return kind.strip()


def _build_vwm_short(params: dict) -> object:
    from nautilus_ext.strategies.vwm_short_signals import VwmShortSignalConfig
    from nautilus_ext.strategies.vwm_short_signals import (
        VolumeWeightedMomentumShortSignalEngine,
    )

    return VolumeWeightedMomentumShortSignalEngine(
        VwmShortSignalConfig(
            mom_len=int(params.get("mom_len", 5)),
            avg_len=int(params.get("avg_len", 20)),
            atr_len=int(params.get("atr_len", 5)),
            atr_pcnt=float(params.get("atr_pcnt", 0.5)),
            setup_len=int(params.get("setup_len", 5)),
        ),
    )


register_signal_engine("vwm_short", _build_vwm_short)
