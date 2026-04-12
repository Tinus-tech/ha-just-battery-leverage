"""Battery trading strategy engine for Just Leverage Battery."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

_LOGGER = logging.getLogger(__name__)

PRICE_UNIT = 1e7  # Zonneplan: electricity_price is in 0.1 nano-euro/kWh → divide by 1e7 for EUR/kWh


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------

@dataclass
class PriceSlot:
    dt: datetime
    price_raw: float
    price_eur: float


@dataclass
class PlannedHour:
    dt: datetime
    action: str            # "charge" | "discharge" | "idle"
    price_eur: float
    simulated_soc: float


@dataclass
class ArbitragePlan:
    hours: list[PlannedHour] = field(default_factory=list)
    price_delta_eur: float = 0.0
    is_profitable: bool = False

    @property
    def next_charge(self) -> PlannedHour | None:
        now = datetime.now(timezone.utc)
        return next((h for h in self.hours if h.action == "charge" and h.dt >= now), None)

    @property
    def next_discharge(self) -> PlannedHour | None:
        now = datetime.now(timezone.utc)
        return next((h for h in self.hours if h.action == "discharge" and h.dt >= now), None)

    @property
    def charge_window_end(self) -> datetime | None:
        start = self.next_charge
        if not start:
            return None
        end_dt = start.dt
        in_block = False
        for h in self.hours:
            if h.dt < start.dt:
                continue
            if h.action == "charge":
                end_dt = h.dt
                in_block = True
            elif in_block:
                break
        from datetime import timedelta
        return end_dt + timedelta(hours=1)

    @property
    def discharge_window_end(self) -> datetime | None:
        start = self.next_discharge
        if not start:
            return None
        end_dt = start.dt
        in_block = False
        for h in self.hours:
            if h.dt < start.dt:
                continue
            if h.action == "discharge":
                end_dt = h.dt
                in_block = True
            elif in_block:
                break
        from datetime import timedelta
        return end_dt + timedelta(hours=1)


@dataclass
class TradeDecision:
    action: str        # "charge" | "discharge" | "idle" | "ups" | "off"
    power_w: int
    reason: str
    plan: ArbitragePlan | None = None


# ---------------------------------------------------------------------------
# PID controller
# ---------------------------------------------------------------------------

@dataclass
class PIDState:
    """Mutable PID state — persists between coordinator ticks."""
    integral: float = 0.0
    last_error: float = 0.0
    last_time: float = field(default_factory=time.monotonic)


@dataclass
class SelfConsumptionDecision:
    power_w: int          # Marstek command: negative = charge, positive = discharge
    grid_power: float     # measured grid power (W), positive = importing
    error: float          # grid_power - target
    in_deadband: bool
    reason: str
    pid_state: PIDState


class PIDController:
    """
    Discrete PID controller for grid-power regulation.

    Sign convention:
      grid_power > 0  →  importing from grid  →  battery should discharge (+)
      grid_power < 0  →  exporting to grid    →  battery should charge (-)

    The output is the battery power command in watts:
      positive = discharge (feed into house / reduce grid import)
      negative = charge    (absorb excess solar / increase grid import)

    Marstek set_passive_mode uses the same convention:
      positive power = discharge, negative power = charge.
    """

    POWER_STEP = 100  # round output to nearest 100W

    def __init__(
        self,
        kp: float,
        ki: float,
        kd: float,
        deadband: float,
        output_min: int,   # most negative value (charge limit, e.g. -2000)
        output_max: int,   # most positive value (discharge limit, e.g. +2000)
    ) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.deadband = deadband
        self.output_min = output_min
        self.output_max = output_max
        # Anti-windup limit: cap integral contribution at ±output_max
        self._anti_windup = abs(output_max) if output_max != 0 else 5000

    def compute(
        self,
        setpoint: float,
        measurement: float,
        state: PIDState,
    ) -> tuple[int, PIDState]:
        """
        Compute the battery power command.

        Returns (power_w, new_state).
        """
        now = time.monotonic()
        dt = max(now - state.last_time, 0.1)  # avoid division by zero

        error = measurement - setpoint  # positive = too much grid import

        # Deadband: no action if close enough to setpoint
        if abs(error) < self.deadband:
            new_integral = state.integral * 0.95  # slow integral decay in deadband
            new_state = PIDState(
                integral=new_integral,
                last_error=error,
                last_time=now,
            )
            return 0, new_state

        # PID terms
        p_term = self.kp * error
        new_integral = state.integral + error * dt
        new_integral = max(-self._anti_windup, min(self._anti_windup, new_integral))
        i_term = self.ki * new_integral
        d_term = self.kd * (error - state.last_error) / dt

        raw_output = p_term + i_term + d_term

        # Round to nearest step, then clamp to limits
        stepped = round(raw_output / self.POWER_STEP) * self.POWER_STEP
        clamped = max(self.output_min, min(self.output_max, stepped))

        new_state = PIDState(
            integral=new_integral,
            last_error=error,
            last_time=now,
        )
        return int(clamped), new_state


# ---------------------------------------------------------------------------
# Self-consumption strategy
# ---------------------------------------------------------------------------

def compute_self_consumption(
    grid_power_w: float,
    target_grid_power_w: float,
    pid: PIDController,
    pid_state: PIDState,
    current_soc: float,
    min_soc: float,
    max_soc: float,
) -> SelfConsumptionDecision:
    """
    PID-based self-consumption: keep grid power at target.

    grid_power_w:
      positive  →  importing (house uses more than it produces)
      negative  →  exporting (solar surplus)
    """
    power_w, new_state = pid.compute(target_grid_power_w, grid_power_w, pid_state)

    in_deadband = power_w == 0 and abs(grid_power_w - target_grid_power_w) < pid.deadband

    # SOC guard: don't charge if already at max, don't discharge if at min
    if power_w < 0 and current_soc >= max_soc:
        power_w = 0
        reason = f"Laden geblokkeerd — SOC {current_soc:.0f}% >= max {max_soc:.0f}%"
    elif power_w > 0 and current_soc <= min_soc:
        power_w = 0
        reason = f"Ontladen geblokkeerd — SOC {current_soc:.0f}% <= min {min_soc:.0f}%"
    elif in_deadband:
        reason = f"In deadband — netafname {grid_power_w:.0f}W ≈ doel {target_grid_power_w:.0f}W"
    elif power_w < 0:
        reason = f"Laden {abs(power_w)}W — netafname {grid_power_w:.0f}W > doel {target_grid_power_w:.0f}W"
    else:
        reason = f"Ontladen {power_w}W — netafname {grid_power_w:.0f}W < doel {target_grid_power_w:.0f}W"

    return SelfConsumptionDecision(
        power_w=power_w,
        grid_power=grid_power_w,
        error=grid_power_w - target_grid_power_w,
        in_deadband=in_deadband,
        reason=reason,
        pid_state=new_state,
    )


def compute_charge_pv(
    grid_power_w: float,
    target_grid_power_w: float,
    peak_shave_limit_w: float,
    pid: PIDController,
    pid_state: PIDState,
    current_soc: float,
    min_soc: float,
    max_soc: float,
    max_discharge_power_w: int,
) -> SelfConsumptionDecision:
    """
    Solar charging + peak shaving.

    Normally runs self-consumption (charge from solar surplus).
    When grid import exceeds peak_shave_limit, overrides to max discharge.
    """
    decision = compute_self_consumption(
        grid_power_w, target_grid_power_w, pid, pid_state, current_soc, min_soc, max_soc
    )

    if grid_power_w > peak_shave_limit_w and current_soc > min_soc:
        # Peak shaving override: discharge at max power
        peak_power = min(max_discharge_power_w, int(grid_power_w - target_grid_power_w))
        peak_power = round(peak_power / 100) * 100
        decision.power_w = max(0, peak_power)
        decision.reason = (
            f"Piekbeveiliging — netafname {grid_power_w:.0f}W > limiet {peak_shave_limit_w:.0f}W"
            f" — ontladen {decision.power_w}W"
        )

    return decision


# ---------------------------------------------------------------------------
# Charge / Sell strategies
# ---------------------------------------------------------------------------

def compute_charge_to_target(
    current_soc: float,
    target_soc: float,
    max_soc: float,
    charge_power_w: int,
) -> TradeDecision:
    """Charge battery to target SOC at maximum power."""
    effective_target = min(target_soc, max_soc)
    if current_soc >= effective_target:
        return TradeDecision(
            action="idle",
            power_w=0,
            reason=f"Doelwaarde bereikt — SOC {current_soc:.0f}% >= doel {effective_target:.0f}%",
        )
    return TradeDecision(
        action="charge",
        power_w=charge_power_w,
        reason=f"Laden naar doel-SOC {effective_target:.0f}% — nu {current_soc:.0f}%",
    )


def compute_sell_to_target(
    current_soc: float,
    target_min_soc: float,
    min_soc: float,
    discharge_power_w: int,
) -> TradeDecision:
    """Discharge battery until minimum SOC is reached."""
    effective_min = max(target_min_soc, min_soc)
    if current_soc <= effective_min:
        return TradeDecision(
            action="idle",
            power_w=0,
            reason=f"Minimale SOC bereikt — SOC {current_soc:.0f}% <= doel {effective_min:.0f}%",
        )
    return TradeDecision(
        action="discharge",
        power_w=discharge_power_w,
        reason=f"Ontladen naar minimum-SOC {effective_min:.0f}% — nu {current_soc:.0f}%",
    )


# ---------------------------------------------------------------------------
# Timed strategy
# ---------------------------------------------------------------------------

@dataclass
class TimedPeriod:
    label: str          # "A", "B", "C"
    start: str          # "HH:MM:SS" local time
    end: str            # "HH:MM:SS" local time
    sub_strategy: str


def _time_in_period(now_time: datetime.time, start_str: str, end_str: str) -> bool:
    """Check if now_time is within [start, end). Handles midnight-crossing periods."""
    def parse(s: str) -> datetime.time:
        parts = s.split(":")
        return datetime.min.replace(
            hour=int(parts[0]), minute=int(parts[1]), second=int(parts[2]) if len(parts) > 2 else 0
        ).time()

    start = parse(start_str)
    end = parse(end_str)

    if start <= end:
        return start <= now_time < end
    # Period wraps midnight (e.g. 23:00 – 07:00)
    return now_time >= start or now_time < end


def resolve_timed_strategy(
    periods: list[TimedPeriod],
    default_strategy: str,
) -> tuple[str, str]:
    """
    Return (sub_strategy, active_period_label) for the current local time.
    Falls back to (default_strategy, "geen") if no period matches.
    """
    now_local = datetime.now().time()
    for period in periods:
        if _time_in_period(now_local, period.start, period.end):
            return period.sub_strategy, period.label
    return default_strategy, "geen"


# ---------------------------------------------------------------------------
# Arbitrage (price-based planning)
# ---------------------------------------------------------------------------

def _parse_slots(forecast_raw: list) -> list[PriceSlot]:
    slots = []
    for item in forecast_raw:
        try:
            dt_str = item.get("datetime", "")
            price_raw = float(item.get("electricity_price", 0))
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            slots.append(PriceSlot(dt=dt, price_raw=price_raw, price_eur=price_raw / PRICE_UNIT))
        except Exception as exc:
            _LOGGER.debug("Overgeslagen forecast item %s: %s", item, exc)
    return slots


def compute_arbitrage_plan(
    forecast_raw: list,
    n_cheap: int,
    n_expensive: int,
    min_price_delta: float,
    current_soc: float,
    min_soc: float,
    max_soc: float,
    charge_power_w: int,
    discharge_power_w: int,
    battery_capacity_wh: int = 2000,
) -> ArbitragePlan:
    plan = ArbitragePlan()
    now = datetime.now(timezone.utc)
    all_slots = _parse_slots(forecast_raw)
    future_slots = [s for s in all_slots if s.dt >= now.replace(minute=0, second=0, microsecond=0)]

    if not future_slots:
        return plan

    sorted_asc = sorted(future_slots, key=lambda s: s.price_raw)
    cheapest_n = {s.dt for s in sorted_asc[:n_cheap]}
    expensive_n = {s.dt for s in sorted_asc[-n_expensive:]}

    plan.price_delta_eur = sorted_asc[-1].price_eur - sorted_asc[0].price_eur
    plan.is_profitable = plan.price_delta_eur >= min_price_delta

    soc = current_soc
    charge_kwh = charge_power_w / 1000
    discharge_kwh = discharge_power_w / 1000
    capacity_kwh = battery_capacity_wh / 1000

    for slot in sorted(future_slots, key=lambda s: s.dt):
        if not plan.is_profitable:
            action = "idle"
        elif slot.dt in cheapest_n and soc < max_soc:
            soc = min(soc + (charge_kwh / capacity_kwh) * 100, max_soc)
            action = "charge"
        elif slot.dt in expensive_n and soc > min_soc:
            soc = max(soc - (discharge_kwh / capacity_kwh) * 100, min_soc)
            action = "discharge"
        else:
            action = "idle"

        plan.hours.append(PlannedHour(
            dt=slot.dt,
            action=action,
            price_eur=slot.price_eur,
            simulated_soc=round(soc, 1),
        ))

    return plan


def parse_zonneplan_forecast(
    forecast_raw: list,
    n_cheap: int,
    n_expensive: int,
    min_price_delta: float = 0.0,
    current_soc: float | None = None,
    min_soc: float = 10.0,
    max_soc: float = 95.0,
    charge_power_w: int = 2000,
    discharge_power_w: int = 2000,
) -> TradeDecision | None:
    if not forecast_raw:
        _LOGGER.warning("Zonneplan forecast is leeg — geen handelsbeslissing mogelijk")
        return None

    plan = compute_arbitrage_plan(
        forecast_raw=forecast_raw,
        n_cheap=n_cheap,
        n_expensive=n_expensive,
        min_price_delta=min_price_delta,
        current_soc=current_soc if current_soc is not None else 50.0,
        min_soc=min_soc,
        max_soc=max_soc,
        charge_power_w=charge_power_w,
        discharge_power_w=discharge_power_w,
    )

    now_hour = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
    current = next(
        (h for h in plan.hours if h.dt.strftime("%Y-%m-%dT%H") == now_hour),
        None,
    )

    if current is None:
        return TradeDecision(action="idle", power_w=0, reason="Huidig uur niet in forecast", plan=plan)

    if not plan.is_profitable:
        reason = f"Delta €{plan.price_delta_eur:.3f}/kWh < drempel €{min_price_delta:.3f}/kWh — geen arbitrage"
        return TradeDecision(action="idle", power_w=0, reason=reason, plan=plan)

    if current.action == "charge":
        reason = f"Goedkoopste {n_cheap} uren — €{current.price_eur:.3f}/kWh"
    elif current.action == "discharge":
        reason = f"Duurste {n_expensive} uren — €{current.price_eur:.3f}/kWh"
    else:
        reason = f"Middenprijs — €{current.price_eur:.3f}/kWh"

    return TradeDecision(action=current.action, power_w=0, reason=reason, plan=plan)


# ---------------------------------------------------------------------------
# SOC guards (shared)
# ---------------------------------------------------------------------------

def should_charge(decision: TradeDecision, current_soc: float, max_soc: float) -> bool:
    if current_soc >= max_soc:
        _LOGGER.info("Laden overgeslagen — SOC %s%% >= max %s%%", current_soc, max_soc)
        return False
    return decision.action == "charge"


def should_discharge(decision: TradeDecision, current_soc: float, min_soc: float) -> bool:
    if current_soc <= min_soc:
        _LOGGER.info("Ontladen overgeslagen — SOC %s%% <= min %s%%", current_soc, min_soc)
        return False
    return decision.action == "discharge"
