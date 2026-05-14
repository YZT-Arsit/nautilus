__all__ = ["NautilusAutoBarDataConnector"]


def __getattr__(name: str):
    if name == "NautilusAutoBarDataConnector":
        from nautilus_ext.connectors.auto_bar_data_connector import NautilusAutoBarDataConnector

        return NautilusAutoBarDataConnector

    raise AttributeError(f"module 'nautilus_ext.connectors' has no attribute {name!r}")
