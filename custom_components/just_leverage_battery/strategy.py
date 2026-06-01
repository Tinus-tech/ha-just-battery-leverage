"""Battery trading strategy engine for Just Leverage Battery."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
    expected_profit_eur: float = 0.0
    unprofitable_reason: str = ""

    @property
    def next_charge(self) -> PlannedHour | None:
        """Next charge hour that has NOT started yet.

        Strictly future: if the current hour is already a charge block, skip it
        and return the next scheduled charge start (avoids "X min ago" display).
        """
        now = datetime.now(timezone.utc)
        return next((h for h in self.hours if h.action == "charge" and h.dt > now), None)

    @property
    def next_discharge(self) -> PlannedHour | None:
        """Next discharge hour that has NOT started yet (see next_charge)."""
        now = datetime.now(timezone.utc)
        return next((h for h in self.hours if h.action == "discharge" and h.dt > now), None)

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


def _frange(start: float, stop: float, step: float):
    """Float range generator."""
    val = start
    while val <= stop + 0.001:
        yield round(val, 1)
        val += step


@dataclass
class _BatteryModel:
    """Discretised battery model for the DP solver.

    Energy convention (grid-side, AC):
      charge_kwh  = kWh drawn from grid per 1h charge slot
      discharge_kwh = kWh delivered to grid per 1h discharge slot

    SOC convention (battery-side, DC):
      charge:   SOC += charge_kwh * efficiency / capacity  (losses on AC→DC)
      discharge: SOC -= discharge_kwh / efficiency / capacity  (losses on DC→AC)

    This ensures round-trip SOC loss equals real efficiency losses and
    the profit calculation uses grid-side energy (what you pay/earn).
    """
    soc_levels: list[float]
    soc_to_idx: dict[float, int]
    charge_soc_delta: float      # SOC% gain per charge slot (after AC→DC losses)
    discharge_soc_delta: float   # SOC% loss per discharge slot (before DC→AC losses)
    charge_kwh: float            # grid kWh per charge slot (cost basis)
    discharge_kwh: float         # grid kWh per discharge slot (revenue basis)
    efficiency: float            # per-leg (sqrt of round-trip)
    min_soc: float
    max_soc: float

    @staticmethod
    def build(min_soc, max_soc, charge_power_w, discharge_power_w,
              capacity_wh, round_trip_efficiency) -> "_BatteryModel":
        capacity_kwh = capacity_wh / 1000
        charge_kwh = charge_power_w / 1000   # AC grid input
        discharge_kwh = discharge_power_w / 1000  # AC grid output
        efficiency = round_trip_efficiency ** 0.5
        soc_levels = list(_frange(min_soc, max_soc, 1.0))
        if not soc_levels or soc_levels[-1] < max_soc:
            soc_levels.append(max_soc)
        return _BatteryModel(
            soc_levels=soc_levels,
            soc_to_idx={s: i for i, s in enumerate(soc_levels)},
            # SOC changes in battery-internal terms:
            charge_soc_delta=(charge_kwh * efficiency / capacity_kwh) * 100,
            discharge_soc_delta=(discharge_kwh / efficiency / capacity_kwh) * 100,
            charge_kwh=charge_kwh,
            discharge_kwh=discharge_kwh,
            efficiency=efficiency,
            min_soc=min_soc,
            max_soc=max_soc,
        )

    def snap_soc(self, soc: float) -> float:
        return min(self.soc_levels, key=lambda s: abs(s - soc))


def _dp_backwards_pass(
    prices: list[float], bm: _BatteryModel, start_soc_idx: int,
) -> tuple[list[list[float]], list[list[str]]]:
    """Run the DP backwards pass. Returns (profit_table, action_table).

    Terminal condition: ending SOC is valued at median price so the DP
    doesn't artificially drain the battery for "free" revenue at end of
    horizon.  Only SOC above start_soc earns terminal value (conservative).
    """
    n_slots = len(prices)
    n_soc = len(bm.soc_levels)
    INF = float("-inf")

    profit = [[INF] * n_soc for _ in range(n_slots + 1)]
    action_table = [["idle"] * n_soc for _ in range(n_slots)]

    # Terminal condition: ending at or above start SOC is free; ending below
    # start SOC is penalised at median price so the DP doesn't artificially
    # drain the battery for "free" revenue at end of horizon.
    start_soc = bm.soc_levels[start_soc_idx]
    median_price = sorted(prices)[len(prices) // 2]
    capacity_kwh = bm.charge_kwh / (bm.charge_soc_delta / 100) / bm.efficiency
    for i, soc in enumerate(bm.soc_levels):
        if soc >= start_soc:
            profit[n_slots][i] = 0.0
        else:
            # Penalise ending below start: must "buy back" the missing energy
            deficit_kwh = (start_soc - soc) / 100 * capacity_kwh
            profit[n_slots][i] = -deficit_kwh * median_price

    for t in range(n_slots - 1, -1, -1):
        price = prices[t]
        for si, soc in enumerate(bm.soc_levels):
            best = profit[t + 1][si]
            best_action = "idle"

            # Charge option (partial allowed up to max_soc)
            max_charge_delta = bm.max_soc - soc
            charge_delta = min(bm.charge_soc_delta, max_charge_delta)
            if charge_delta > 0.5:  # ignore <0.5% headroom (rounding noise)
                scale = charge_delta / bm.charge_soc_delta
                new_soc = soc + charge_delta
                ni = bm.soc_to_idx.get(bm.snap_soc(new_soc))
                if ni is not None:
                    val = -price * bm.charge_kwh * scale + profit[t + 1][ni]
                    if val > best:
                        best, best_action = val, "charge"

            # Discharge option (partial allowed down to min_soc)
            max_discharge_delta = soc - bm.min_soc
            discharge_delta = min(bm.discharge_soc_delta, max_discharge_delta)
            if discharge_delta > 0.5:
                scale = discharge_delta / bm.discharge_soc_delta
                new_soc_d = soc - discharge_delta
                ni = bm.soc_to_idx.get(bm.snap_soc(new_soc_d))
                if ni is not None:
                    val = price * bm.discharge_kwh * scale + profit[t + 1][ni]
                    if val > best:
                        best, best_action = val, "discharge"

            profit[t][si] = best
            action_table[t][si] = best_action

    return profit, action_table


def _dp_forward_trace(
    bm: _BatteryModel, action_table: list[list[str]],
    start_soc: float, n_slots: int, is_profitable: bool,
) -> list[tuple[str, float]]:
    """Trace optimal actions forward. Returns list of (action, soc) tuples."""
    result = []
    soc = start_soc
    for t in range(n_slots):
        si = bm.soc_to_idx[bm.snap_soc(soc)]
        action = action_table[t][si] if is_profitable else "idle"
        if action == "charge":
            soc = min(soc + bm.charge_soc_delta, bm.max_soc)
        elif action == "discharge":
            soc = max(soc - bm.discharge_soc_delta, bm.min_soc)
        result.append((action, round(soc, 1)))
    return result


def _filter_low_delta_cycles(
    actions: list[str], prices: list[float], min_delta: float,
) -> list[str]:
    """Set to idle any charge/discharge pair where avg delta < min_delta.

    A cycle = one block of consecutive 'charge' slots followed by one block
    of consecutive 'discharge' slots (with any idles between).
    """
    result = list(actions)
    n = len(result)
    i = 0
    while i < n:
        while i < n and result[i] != "charge":
            i += 1
        if i >= n:
            break
        c_start = i
        while i < n and result[i] == "charge":
            i += 1
        c_end = i
        # Skip idles to find the paired discharge block (stop if another charge comes first)
        while i < n and result[i] not in ("charge", "discharge"):
            i += 1
        if i >= n or result[i] == "charge":
            continue
        d_start = i
        while i < n and result[i] == "discharge":
            i += 1
        d_end = i
        avg_c = sum(prices[c_start:c_end]) / (c_end - c_start)
        avg_d = sum(prices[d_start:d_end]) / (d_end - d_start)
        if (avg_d - avg_c) < min_delta:
            for k in range(c_start, c_end):
                result[k] = "idle"
            for k in range(d_start, d_end):
                result[k] = "idle"
            _LOGGER.info(
                "Cyclus %d–%d uitgefilterd — delta €%.3f < drempel €%.3f",
                c_start, d_end - 1, avg_d - avg_c, min_delta,
            )
    return result


def _resimulate_soc(
    actions: list[str], bm: _BatteryModel, start_soc: float,
) -> list[tuple[str, float]]:
    """Replay an action list and produce (action, soc) tuples."""
    result = []
    soc = start_soc
    for action in actions:
        if action == "charge":
            soc = min(soc + bm.charge_soc_delta, bm.max_soc)
        elif action == "discharge":
            soc = max(soc - bm.discharge_soc_delta, bm.min_soc)
        result.append((action, round(soc, 1)))
    return result


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
    battery_capacity_wh: int = 5120,
    round_trip_efficiency: float = 0.90,
) -> ArbitragePlan:
    """
    Dynamic programming arbitrage planner.

    For each hour and SOC level, finds the optimal charge/discharge/idle action
    that maximises total profit over the forecast horizon, accounting for
    round-trip efficiency losses.

    Energy convention: charge_power_w and discharge_power_w are AC grid-side
    values.  Losses are applied symmetrically to SOC transitions:
      charge:    SOC += (charge_kWh * eff / capacity) * 100
      discharge: SOC -= (discharge_kWh / eff / capacity) * 100
    Profit is calculated at grid-side prices (what you pay and earn).
    """
    plan = ArbitragePlan()
    now = datetime.now(timezone.utc)
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    all_slots = _parse_slots(forecast_raw)
    future_slots = sorted(
        [s for s in all_slots if s.dt >= current_hour],
        key=lambda s: s.dt,
    )

    if not future_slots:
        return plan

    sorted_by_price = sorted(future_slots, key=lambda s: s.price_raw)
    plan.price_delta_eur = sorted_by_price[-1].price_eur - sorted_by_price[0].price_eur

    bm = _BatteryModel.build(min_soc, max_soc, charge_power_w, discharge_power_w,
                             battery_capacity_wh, round_trip_efficiency)
    start_soc = bm.snap_soc(current_soc)
    start_soc_idx = bm.soc_to_idx[start_soc]

    prices = [s.price_eur for s in future_slots]
    profit, action_table = _dp_backwards_pass(prices, bm, start_soc_idx)

    # Expected profit is relative to terminal value at start_soc (= 0)
    expected_profit = profit[0][start_soc_idx]
    plan.expected_profit_eur = round(expected_profit, 4)
    plan.is_profitable = expected_profit > 0 and plan.price_delta_eur >= min_price_delta

    if not plan.is_profitable:
        if plan.price_delta_eur < min_price_delta:
            plan.unprofitable_reason = (
                f"Globale delta €{plan.price_delta_eur:.3f}/kWh < drempel €{min_price_delta:.3f}/kWh"
            )
        elif expected_profit <= 0:
            plan.unprofitable_reason = (
                f"DP vond geen winstgevende route bij SOC {current_soc:.0f}% "
                f"— SOC-bereik te krap of efficiency-verliezen overtreffen marge"
            )

    trace = _dp_forward_trace(bm, action_table, start_soc, len(future_slots), plan.is_profitable)

    # Per-cycle filter: drop cycles where avg(discharge) - avg(charge) < min_delta.
    # Even if DP's total profit is positive, each cycle must earn its own margin.
    if plan.is_profitable and min_price_delta > 0:
        raw_actions = [a for a, _ in trace]
        filtered = _filter_low_delta_cycles(raw_actions, prices, min_price_delta)
        trace = _resimulate_soc(filtered, bm, start_soc)

    for slot, (action, soc) in zip(future_slots, trace):
        plan.hours.append(PlannedHour(
            dt=slot.dt, action=action, price_eur=slot.price_eur, simulated_soc=soc,
        ))

    # Re-check profitability after filtering: if all cycles were dropped, plan is not profitable
    if plan.is_profitable and not any(h.action != "idle" for h in plan.hours):
        plan.is_profitable = False
        plan.unprofitable_reason = (
            f"Alle DP-cycli onder per-cyclus drempel €{min_price_delta:.3f}/kWh — uitgefilterd"
        )

    # Diagnostic logging
    charge_hours = [(h.dt.strftime("%H:%M"), h.price_eur) for h in plan.hours if h.action == "charge"]
    discharge_hours = [(h.dt.strftime("%H:%M"), h.price_eur) for h in plan.hours if h.action == "discharge"]
    _LOGGER.info(
        "DP plan: %d slots, SOC %.0f%%, delta €%.3f, verwachte winst €%.4f, "
        "winstgevend=%s, laden=%s, ontladen=%s",
        len(future_slots), current_soc, plan.price_delta_eur, expected_profit,
        plan.is_profitable,
        [(t, f"€{p:.3f}") for t, p in charge_hours],
        [(t, f"€{p:.3f}") for t, p in discharge_hours],
    )

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
        reason = plan.unprofitable_reason or f"Niet winstgevend (delta €{plan.price_delta_eur:.3f}/kWh)"
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
