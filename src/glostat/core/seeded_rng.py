from __future__ import annotations

import hashlib
import random
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

# MOET A7: SeededRng — same (ticker, date, seed) ⇒ same verdict (INV-GS-010).
# SHA256-derivable so the same (universe, date, namespace) always reproduces.

_NS_BYTES: Final[int] = 32
_FLOAT_DIVISOR: Final[float] = float(1 << 53)


def derive_seed(*parts: str | int | bytes) -> int:
    h = hashlib.sha256()
    for p in parts:
        if isinstance(p, int):
            h.update(p.to_bytes(8, "big", signed=False))
        elif isinstance(p, bytes):
            h.update(p)
        else:
            h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return int.from_bytes(h.digest()[:8], "big", signed=False)


@dataclass(slots=True)
class SeededRng:
    namespace: str
    seed: int
    _rand: random.Random | None = None

    def __post_init__(self) -> None:
        self._rand = random.Random(self.seed ^ derive_seed(self.namespace))

    @classmethod
    def from_parts(cls, namespace: str, *parts: str | int | bytes) -> SeededRng:
        return cls(namespace=namespace, seed=derive_seed(namespace, *parts))

    def random(self) -> float:
        assert self._rand is not None
        return self._rand.random()

    def randrange(self, start: int, stop: int) -> int:
        assert self._rand is not None
        return self._rand.randrange(start, stop)

    def choice(self, seq: Iterable[object]) -> object:
        assert self._rand is not None
        return self._rand.choice(list(seq))

    def shuffle(self, seq: list[object]) -> None:
        assert self._rand is not None
        self._rand.shuffle(seq)

    def gauss(self, mu: float, sigma: float) -> float:
        assert self._rand is not None
        return self._rand.gauss(mu, sigma)

    def child(self, sub_namespace: str) -> SeededRng:
        return SeededRng.from_parts(f"{self.namespace}/{sub_namespace}", self.seed)

    def derived_float_from(self, *parts: str | int | bytes) -> float:
        digest = hashlib.sha256(self.namespace.encode())
        digest.update(self.seed.to_bytes(8, "big"))
        for p in parts:
            digest.update(str(p).encode())
            digest.update(b"\x00")
        word = struct.unpack(">Q", digest.digest()[:8])[0]
        return (word >> 11) / _FLOAT_DIVISOR
