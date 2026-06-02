from __future__ import annotations

from collections.abc import Callable

from nautilus_ext.strategies.interfaces.strategy_schema import StrategySpecV2


_SIGNAL_ENGINE_CLASSES: dict[str, object] = {}
_DEFAULTS_REGISTERED = False


def register_signal_engine(name: str, cls=None):
    normalized = _normalize_name(name)

    def decorator(target):
        _validate_engine_target(target)
        _SIGNAL_ENGINE_CLASSES[normalized] = target
        return target

    if cls is None:
        return decorator
    return decorator(cls)


def build_signal_engine(spec_or_name, params: dict | None = None) -> object:
    _register_defaults()
    if isinstance(spec_or_name, StrategySpecV2):
        name = spec_or_name.name
        engine_params = spec_or_name.params
    elif isinstance(spec_or_name, dict):
        spec = StrategySpecV2.from_dict(spec_or_name)
        name = spec.name
        engine_params = spec.params
    else:
        name = str(spec_or_name)
        engine_params = dict(params or {})

    normalized = _normalize_name(name)
    target = _SIGNAL_ENGINE_CLASSES.get(normalized)
    if target is None:
        raise ValueError(
            f"Unknown signal engine {name!r}. "
            f"Available signal engines: {available_signal_engines()}."
        )
    return _build_from_target(target, engine_params)


def available_signal_engines() -> list[str]:
    _register_defaults()
    return sorted(_SIGNAL_ENGINE_CLASSES)


def get_signal_engine_class(name: str):
    _register_defaults()
    normalized = _normalize_name(name)
    if normalized not in _SIGNAL_ENGINE_CLASSES:
        raise ValueError(
            f"Unknown signal engine {name!r}. "
            f"Available signal engines: {available_signal_engines()}."
        )
    return _SIGNAL_ENGINE_CLASSES[normalized]


def _build_from_target(target, params: dict) -> object:
    if hasattr(target, "from_params"):
        engine = target.from_params(dict(params))
    elif isinstance(target, type):
        try:
            engine = target(**dict(params))
        except TypeError:
            engine = target(dict(params))
    else:
        engine = target(dict(params))
    _validate_engine_instance(engine)
    return engine


def _register_defaults() -> None:
    global _DEFAULTS_REGISTERED
    if _DEFAULTS_REGISTERED:
        return
    _DEFAULTS_REGISTERED = True

    def build_vwm_short(params: dict):
        from nautilus_ext.strategies.vwm_short_components import VwmShortSignalConfig
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

    register_signal_engine("vwm_short", build_vwm_short)


def _validate_engine_target(target) -> None:
    if not callable(target):
        raise TypeError("Signal engine target must be callable.")


def _validate_engine_instance(engine) -> None:
    missing = [name for name in ("update", "reset") if not callable(getattr(engine, name, None))]
    if missing:
        raise TypeError(f"Signal engine is missing required methods: {missing}.")


def _normalize_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Signal engine name must be a non-empty string.")
    return name.strip()
