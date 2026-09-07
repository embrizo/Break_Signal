"""Algorithm parameters. Defaults mirror the STRICT settings in the Pine script."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Params:
    pivot_len_auto: bool = True
    pivot_len: int = 5
    use_fine_pivots: bool = True
    pivot_len_fine: int = 3
    max_pivots: int = 10
    max_age: int = 400
    look_max: int = 300
    min_bars: int = 10
    min_touches: int = 3
    max_violations: int = 0
    atr_valid: float = 0.10
    atr_touch: float = 0.25
    max_dist: float = 12.0
    max_lines: int = 3
    atr_break: float = 0.30
    use_volume: bool = True
    vol_mult: float = 1.5
    use_body: bool = True
    body_min: float = 0.5
    two_bar: bool = False

    def resolved_pivot_len(self, tf_seconds: int) -> int:
        """Auto-tune pivot lookback by timeframe, exactly as the Pine script."""
        if not self.pivot_len_auto:
            return self.pivot_len
        if tf_seconds >= 86_400:   # >= 1D
            return 5
        if tf_seconds >= 14_400:   # >= 4H
            return 8
        return 10
