"""Compare a new observation with the previous one and name what changed."""

from __future__ import annotations

from dataclasses import dataclass

from .store import Observation


@dataclass(frozen=True)
class Change:
    kind: str
    old_cents: int | None
    new_cents: int | None
    pct_change: float | None


def detect_changes(
    previous: Observation | None,
    price_cents: int,
    in_stock: bool | None,
    target_cents: int | None,
    drop_threshold_pct: float,
) -> list[Change]:
    changes: list[Change] = []

    if previous is not None and previous.price_cents != price_cents and previous.price_cents > 0:
        pct = round((price_cents - previous.price_cents) / previous.price_cents * 100, 2)
        if pct <= -drop_threshold_pct:
            changes.append(Change("price_drop", previous.price_cents, price_cents, pct))
        elif pct > 0:
            changes.append(Change("price_rise", previous.price_cents, price_cents, pct))

    if previous is not None and previous.in_stock is not None and in_stock is not None:
        if in_stock and not previous.in_stock:
            changes.append(Change("back_in_stock", None, None, None))
        elif previous.in_stock and not in_stock:
            changes.append(Change("out_of_stock", None, None, None))

    # Alert once when the price first crosses the target, not on every run below it.
    if target_cents is not None and price_cents <= target_cents:
        was_above = previous is None or previous.price_cents > target_cents
        if was_above:
            changes.append(Change("target_hit", target_cents, price_cents, None))

    return changes
