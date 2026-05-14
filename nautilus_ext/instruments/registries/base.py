from abc import ABC
from abc import abstractmethod

from nautilus_ext.instruments.instrument_profile import InstrumentProfile


class InstrumentRegistrySource(ABC):
    @abstractmethod
    def load_profiles(self) -> list[InstrumentProfile]:
        raise NotImplementedError


class StaticInstrumentRegistrySource(InstrumentRegistrySource):
    def __init__(self, profiles: list[InstrumentProfile]):
        self._profiles = profiles

    def load_profiles(self) -> list[InstrumentProfile]:
        return list(self._profiles)
