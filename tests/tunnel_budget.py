"""Budget policy for the private two-tunnel experiment."""
from dataclasses import dataclass

TOTAL = 900
SHARE = TOTAL // 2
RESERVE = 25
SPLIT = (SHARE, SHARE)


@dataclass
class Budget:
    rates: tuple[int, int] = SPLIT
    quiet: tuple[int, int] = (0, 0)

    def sample(self, mbps: tuple[float, float], queued: tuple[int, int]) -> tuple[int, int]:
        active = tuple(rate >= 10 or packets > 0 for rate, packets in zip(mbps, queued, strict=True))
        self.quiet = tuple(0 if busy else min(count+1, 3)
                           for count, busy in zip(self.quiet, active, strict=True))
        target = SPLIT
        for index in range(2):
            other = 1-index
            if self.quiet[other] < 3:
                continue
            if queued[index] > 0 or mbps[index] >= .75*SHARE:
                target = (TOTAL-RESERVE, RESERVE) if index == 0 else (RESERVE, TOTAL-RESERVE)
                break
        return target

    def updates(self, target: tuple[int, int]):
        if target not in (SPLIT, (TOTAL-RESERVE, RESERVE), (RESERVE, TOTAL-RESERVE)):
            raise ValueError('unregistered tunnel allocation')
        # Apply decreases first so every intermediate state respects the total.
        for index in sorted(range(2), key=lambda i: target[i]-self.rates[i]):
            if target[index] != self.rates[index]:
                yield index, target[index]
