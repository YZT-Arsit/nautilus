from nautilus_ext.instruments.registries import binance_futures
from nautilus_ext.instruments.registries import cfd
from nautilus_ext.instruments.registries import commodities
from nautilus_ext.instruments.registries import currency_pairs
from nautilus_ext.instruments.registries import equities
from nautilus_ext.instruments.registries import futures_contracts
from nautilus_ext.instruments.registries import indices
from nautilus_ext.instruments.registries import options
from nautilus_ext.instruments.registries import synthetics


def load_all_default_profiles():
    profiles = []
    for source in [
        binance_futures,
        currency_pairs,
        futures_contracts,
        equities,
        options,
        cfd,
        commodities,
        indices,
        synthetics,
    ]:
        profiles.extend(source.load_profiles())
    return profiles
