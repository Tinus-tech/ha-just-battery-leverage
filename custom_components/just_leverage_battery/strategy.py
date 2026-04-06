"""Battery trading strategy engine for Just Leverage Battery."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

_LOGGER = logging.getLogger(__name__)


@dataclass
class PriceSlot:
    """Represents a single hourly price slot."""
    dt: datetime
    price: float  # in euro/kWh (normalized)


@dataclass
class TradeDecision:
    """Result of the strategy engine for the current hour."""
    action: str           # "charge", "discharge", "idle", "ups", "off"
    power_w: int          # Watt (positive = relevant power, sign handled per action)
    reason: str           # Human-readable explanation


def parse_zonneplan_forecast(forecast_raw: list, n_cheap: int, n_expensive: int) -> TradeDecision | None:
    """
    Parse the Zonneplan forecast attribute and determine what to do this hour.

    Zonneplan forecast items look like:
      {"datetime": "2024-01-01T12:00:00.000Z", "electricity_price": 123456789}
    electricity_price is in nano-euro/kWh (divide by 1e9 to get €/kWh) or
    in some versions it's already in cent/kWh — we normalise by sorting only,
    so the unit doesn't matter for ranking.
    """
    if not forecast_raw:
        _LOGGER.warning("Zonneplan forecast is empty — cannot make a trade decision")
        return None

    now_hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")

    slots: list[PriceSlot] = []
    for item in forecast_raw:
        try:
            dt_str = item.get("datetime", "")
            price = float(item.get("electricity_price", 0))
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            slots.append(PriceSlot(dt=dt, price=price))
        except Exception as exc:
            _LOGGER.debug("Skipping malformed forecast item %s: %s", item, exc)
            continue

    if not slots:
        return None

    # Sort by price
    sorted_asc = sorted(slots, key=lambda s: s.price)
    cheapest_hours = {s.dt.strftime("%Y-%m-%dT%H") for s in sorted_asc[:n_cheap]}
    expensive_hours = {s.dt.strftime("%Y-%m-%dT%H") for s in sorted_asc[-n_expensive:]}

    if now_hour in cheapest_hours:
        slot = next(s for s in sorted_asc if s.dt.strftime("%Y-%m-%dT%H") == now_hour)
        return TradeDecision(
            action="charge",
            power_w=0,  # filled in by coordinator
            reason=f"Goedkoopste {n_cheap} uren — prijs rank #{sorted_asc.index(slot)+1}/{len(slots)}",
        )
    elif now_hour in expensive_hours:
        slot = next(s for s in sorted_asc if s.dt.strftime("%Y-%m-%dT%H") == now_hour)
        return TradeDecision(
            action="discharge",
            power_w=0,
            reason=f"Duurste {n_expensive} uren — prijs rank #{sorted_asc.index(slot)+1}/{len(slots)}",
        )
    else:
        return TradeDecision(
            action="idle",
            power_w=0,
            reason="Geen actie — prijs in middensegment",
        )


def should_charge(decision: TradeDecision, current_soc: float, max_soc: float) -> bool:
    """Guard: only charge if battery has room."""
    if current_soc >= max_soc:
        _LOGGER.info("Laden overgeslagen — SOC %s%% >= max %s%%", current_soc, max_soc)
        return False
    return decision.action == "charge"


def should_discharge(decision: TradeDecision, current_soc: float, min_soc: float) -> bool:
    """Guard: only discharge if enough energy in battery. No export (UPS reserve)."""
    if current_soc <= min_soc:
        _LOGGER.info("Ontladen overgeslagen — SOC %s%% <= min %s%%", current_soc, min_soc)
        return False
    return decision.action == "discharge"
