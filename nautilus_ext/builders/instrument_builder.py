class InstrumentBuilder:
    @staticmethod
    def from_existing(instrument):
        return instrument

    @staticmethod
    def require_existing_instrument(instrument):
        if instrument is None:
            raise ValueError(
                "A Nautilus instrument instance is required. "
                "Pass an existing instrument, for example from TestInstrumentProvider "
                "or your internal instrument factory."
            )
        return instrument
