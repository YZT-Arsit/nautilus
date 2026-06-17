"""Offline mock message source for the live adapter (no network).

``MockMessageSource`` replays a pre-canned list of raw Binance WS dicts, so the
normalizer + downstream code can be exercised deterministically in unit tests
without any socket.  It is the injectable seam a real WS transport will replace
in a later milestone.
"""
from __future__ import annotations

from typing import Any, Iterator


class MockMessageSource:
    """Yields ``(message, receive_time_ns)`` tuples from canned data.

    ``receive_times_ns`` (optional) supplies a synthetic local receipt timestamp
    per message — useful for quote messages that carry no exchange time.  When
    omitted, the receive time is ``None``.
    """

    def __init__(self, messages, *, receive_times_ns=None) -> None:
        self._messages = list(messages)
        if receive_times_ns is not None:
            receive_times_ns = list(receive_times_ns)
            if len(receive_times_ns) != len(self._messages):
                raise ValueError("receive_times_ns must match messages length")
        self._recv = receive_times_ns

    def __len__(self) -> int:
        return len(self._messages)

    def __iter__(self) -> Iterator[tuple[Any, int | None]]:
        for i, msg in enumerate(self._messages):
            recv = self._recv[i] if self._recv is not None else None
            yield (msg, recv)
