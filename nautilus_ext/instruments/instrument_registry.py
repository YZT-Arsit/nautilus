from nautilus_ext.instruments.instrument_profile import InstrumentProfile


class InstrumentRegistry:
    def __init__(self):
        self._profiles: list[InstrumentProfile] = []

    def register(self, profile: InstrumentProfile):
        self._profiles.append(profile)

    def get(
        self,
        symbol: str,
        venue: str | None = None,
        instrument_type: str | None = None,
    ) -> InstrumentProfile:
        normalized_symbol = self._normalize_symbol(symbol)
        normalized_venue = venue.upper() if venue is not None else None
        matches = [
            profile
            for profile in self._profiles
            if self._normalize_symbol(profile.symbol) == normalized_symbol
            and (normalized_venue is None or profile.venue.upper() == normalized_venue)
            and (instrument_type is None or profile.instrument_type == instrument_type)
        ]

        if not matches:
            raise ValueError(
                f"No instrument profile found for symbol={symbol!r}, venue={venue!r}, "
                f"instrument_type={instrument_type!r}. Available symbols: {self.list_symbols()}"
            )

        if len(matches) > 1 and venue is None:
            options = [f"{profile.symbol}.{profile.venue} ({profile.instrument_type})" for profile in matches]
            raise ValueError(
                f"Multiple profiles found for symbol={symbol!r}. Specify venue. "
                f"Options: {options}"
            )

        return matches[0]

    def list_symbols(self) -> list[str]:
        return sorted({profile.symbol for profile in self._profiles})

    def list_profiles(self) -> list[InstrumentProfile]:
        return list(self._profiles)

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        return str(symbol).upper().replace("/", "")
