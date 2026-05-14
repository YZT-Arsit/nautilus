__all__ = ["BarTypeFactory", "InstrumentBuilder", "NautilusBarBuilder"]


def __getattr__(name: str):
    if name == "InstrumentBuilder":
        from nautilus_ext.builders.instrument_builder import InstrumentBuilder

        return InstrumentBuilder
    if name == "BarTypeFactory":
        from nautilus_ext.builders.bar_type_factory import BarTypeFactory

        return BarTypeFactory
    if name == "NautilusBarBuilder":
        from nautilus_ext.builders.bar_builder import NautilusBarBuilder

        return NautilusBarBuilder

    raise AttributeError(f"module 'nautilus_ext.builders' has no attribute {name!r}")
