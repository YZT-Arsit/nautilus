from nautilus_ext.instruments.registries import binance_futures
from nautilus_ext.instruments.registries import currency_pairs
from nautilus_ext.instruments.registries import equities
from nautilus_ext.instruments.registries import futures_contracts
from nautilus_ext.instruments.registries import options


def load_all_default_profiles():
    profiles = []
    for source in [
        binance_futures,
        currency_pairs,
        futures_contracts,
        equities,
        options,
    ]:
        profiles.extend(source.load_profiles())
    return profiles
