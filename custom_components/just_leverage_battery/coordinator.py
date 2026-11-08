"""DataUpdateCoordinator for Just Leverage Battery."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    CONF_MARSTEK_DEVICE_ID,
    CONF_PRICE_SENSOR,
    CONF_PRICE_FORECAST_ATTR,
    CONF_CHEAP_HOURS,
    CONF_EXPENSIVE_HOURS,
    CONF_MIN_SOC,
    CONF_MAX_SOC,
    CONF_CHARGE_POWER,
    CONF_DISCHARGE_POWER,
    CONF_PRE_CHARGE_MINUTES,
    CONF_STRATEGY,
    CONF_MIN_PRICE_DELTA,
    CONF_GRID_POWER_SENSOR,
    CONF_TARGET_GRID_POWER,
    CONF_PID_KP,
    CONF_PID_KI,
    CONF_PID_KD,
    CONF_PID_DEADBAND,
    CONF_PEAK_SHAVE_LIMIT,
    CONF_TARGET_SOC,
    CONF_TIMED_PERIOD_A_START,
    CONF_TIMED_PERIOD_A_END,
    CONF_TIMED_PERIOD_A_STRATEGY,
    CONF_TIMED_PERIOD_B_ENABLED,
    CONF_TIMED_PERIOD_B_START,
    CONF_TIMED_PERIOD_B_END,
    CONF_TIMED_PERIOD_B_STRATEGY,
    CONF_TIMED_PERIOD_C_ENABLED,
    CONF_TIMED_PERIOD_C_START,
    CONF_TIMED_PERIOD_C_END,
    CONF_TIMED_PERIOD_C_STRATEGY,
    DEFAULT_CHEAP_HOURS,
    DEFAULT_EXPENSIVE_HOURS,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_CHARGE_POWER,
    DEFAULT_DISCHARGE_POWER,
    DEFAULT_PRE_CHARGE_MINUTES,
    DEFAULT_PRE_ACTION_FLIP_COOLDOWN,
    DEFAULT_MIN_ACTION_SECONDS,
    DEFAULT_STICKY_SAFETY_MARGIN,
    DEFAULT_SOC_TOP_UP_DEADBAND,
    DEFAULT_PRICE_FORECAST_ATTR,
    DEFAULT_STRATEGY,
    DEFAULT_MIN_PRICE_DELTA,
    DEFAULT_TARGET_GRID_POWER,
    DEFAULT_PID_KP,
    DEFAULT_PID_KI,
    DEFAULT_PID_KD,
    DEFAULT_PID_DEADBAND,
    DEFAULT_PEAK_SHAVE_LIMIT,
    DEFAULT_TARGET_SOC,
    DEFAULT_TIMED_PERIOD_A_START,
    DEFAULT_TIMED_PERIOD_A_END,
    DEFAULT_TIMED_PERIOD_A_STRATEGY,
    MARSTEK_MODBUS_DOMAIN,
    KEY_BATTERY_SOC,
    KEY_FORCE_MODE,
    KEY_USER_WORK_MODE,
    KEY_SET_CHARGE_POWER,
    KEY_SET_DISCHARGE_POWER,
    FORCE_MODE_STANDBY,
    FORCE_MODE_CHARGE,
    FORCE_MODE_DISCHARGE,
    STRATEGY_ARBITRAGE,
    STRATEGY_UPS,
    STRATEGY_OFF,
    STRATEGY_SELF_CONSUMPTION,
    STRATEGY_CHARGE_PV,
    STRATEGY_CHARGE,
    STRATEGY_SELL,
    STRATEGY_TIMED,
    UPDATE_INTERVAL,
    PID_UPDATE_INTERVAL,
)
from .strategy import (
    parse_zonneplan_forecast,
    should_charge,
    should_discharge,
    TradeDecision,
    ArbitragePlan,
    PIDController,
    PIDState,
    SelfConsumptionDecision,
    TimedPeriod,
    compute_self_consumption,
    compute_charge_pv,
    compute_charge_to_target,
    compute_sell_to_target,
    resolve_timed_strategy,
)

_LOGGER = logging.getLogger(__name__)

SOC_UNAVAILABLE_REASON = (
    "SOC niet beschikbaar — batterij gestopt (kan grenzen niet verifiëren)"
)

# Persistent diagnostic log — survives HA restarts and log rotation.
# Each instance appends; entry_id distinguishes instances.
_DIAG_PATH = "/config/jlb_diag.log"
_DIAG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB → rotate to .old


def _diag_write(entry_id: str, message: str) -> None:
    """Append a timestamped diagnostic line to the persistent log."""
    try:
        # Simple rotation: rename file when too big
        try:
            if os.path.getsize(_DIAG_PATH) > _DIAG_MAX_BYTES:
                os.replace(_DIAG_PATH, _DIAG_PATH + ".old")
        except OSError:
            pass
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        short_id = entry_id[:8]
        with open(_DIAG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts} [{short_id}] {message}\n")
    except OSError as exc:
        _LOGGER.debug("Kon diag-log niet schrijven: %s", exc)


PID_STRATEGIES = (STRATEGY_SELF_CONSUMPTION, STRATEGY_CHARGE_PV)


class MarstekBatteryTraderCoordinator(DataUpdateCoordinator):
    """Coordinates the battery trading logic."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        self.config_entry = config_entry
        self._strategy: str = config_entry.data.get(CONF_STRATEGY, DEFAULT_STRATEGY)

        # Shared state
        self._last_action: str = "unknown"
        self._last_reason: str = ""
        self._last_soc: float | None = None
        self._last_plan: ArbitragePlan | None = None
        self._last_decision: TradeDecision | None = None
        self._timed_active_period: str = "geen"

        # Last commanded direction (standby|charge|discharge) — used to detect
        # transitions so _last_active_direction/_last_active_at update correctly.
        self._current_direction: str = "standby"

        # Last ACTIVE direction (charge|discharge) — survives standby ticks so the
        # pre-action cooldown can detect "we recently did opposite". Updated only
        # on transitions to an active direction; persists across idle gaps.
        self._last_active_direction: str | None = None
        self._last_active_at: float | None = None

        # SOC top-up hysteresis (Schmitt trigger): once SOC reaches max_soc, block
        # further "charge" decisions until SOC drops meaningfully below max_soc
        # (DEFAULT_SOC_TOP_UP_DEADBAND). Prevents DP from re-planning a top-up charge
        # for every tiny SOC dip right at the ceiling — same mechanism as a
        # thermostat deadband, requires no user configuration.
        self._topped_up: bool = False
        # Mirror at the bottom for symmetry (blocks "discharge" right after hitting min_soc)
        self._bottomed_out: bool = False

        # Profit tracking (resets daily at midnight)
        self._today_charge_cost: float = 0.0
        self._today_discharge_revenue: float = 0.0
        self._today_charge_kwh: float = 0.0
        self._today_discharge_kwh: float = 0.0
        self._today_date: str = datetime.now().strftime("%Y-%m-%d")
        self._total_profit: float = 0.0

        # PID state
        self._pid_state: PIDState = PIDState()
        self._pid_controller: PIDController | None = None
        self._pid_unsub = None
        self._last_pid_decision: SelfConsumptionDecision | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    # ------------------------------------------------------------------
    # Strategy management
    # ------------------------------------------------------------------

    @property
    def strategy(self) -> str:
        return self._strategy

    @strategy.setter
    def strategy(self, value: str) -> None:
        self._strategy = value
        self._manage_pid_timer()
        _LOGGER.info("Strategie gewijzigd naar: %s", value)

    def _is_pid_strategy(self, strategy: str) -> bool:
        return strategy in PID_STRATEGIES

    def _manage_pid_timer(self) -> None:
        """Start or stop the 15s PID tick timer depending on the active strategy."""
        if self._is_pid_strategy(self._strategy):
            if self._pid_unsub is None:
                self._pid_state = PIDState()
                self._rebuild_pid_controller()
                self._pid_unsub = async_track_time_interval(
                    self.hass,
                    self._async_pid_tick,
                    timedelta(seconds=PID_UPDATE_INTERVAL),
                )
                _LOGGER.info("PID timer gestart (interval: %ds)", PID_UPDATE_INTERVAL)
        else:
            if self._pid_unsub is not None:
                self._pid_unsub()
                self._pid_unsub = None
                _LOGGER.info("PID timer gestopt")

    def _rebuild_pid_controller(self) -> None:
        charge_power = int(self._get_conf(CONF_CHARGE_POWER, DEFAULT_CHARGE_POWER))
        discharge_power = int(self._get_conf(CONF_DISCHARGE_POWER, DEFAULT_DISCHARGE_POWER))
        self._pid_controller = PIDController(
            kp=float(self._get_conf(CONF_PID_KP, DEFAULT_PID_KP)),
            ki=float(self._get_conf(CONF_PID_KI, DEFAULT_PID_KI)),
            kd=float(self._get_conf(CONF_PID_KD, DEFAULT_PID_KD)),
            deadband=float(self._get_conf(CONF_PID_DEADBAND, DEFAULT_PID_DEADBAND)),
            output_min=-charge_power,
            output_max=discharge_power,
        )

    async def _async_pid_tick(self, _now) -> None:
        """Called every 15s when a PID strategy is active."""
        if self._strategy == STRATEGY_SELF_CONSUMPTION:
            await self._run_self_consumption()
        elif self._strategy == STRATEGY_CHARGE_PV:
            await self._run_charge_pv()
        self.async_set_updated_data(self._state_dict())

    def async_cleanup(self) -> None:
        """Cancel the PID timer on integration unload."""
        if self._pid_unsub is not None:
            self._pid_unsub()
            self._pid_unsub = None

    # ------------------------------------------------------------------
    # Main coordinator update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        strategy = self._strategy

        if strategy == STRATEGY_OFF:
            # Hands-off mode: do NOT write to Modbus. User can manually control
            # the Marstek (via the marstek_modbus selects) without interference.
            # Reset direction state so a future strategy switch starts fresh.
            self._current_direction = "standby"
            self._last_action = "off"
            self._last_reason = "Strategie staat op Uit — geen Modbus writes (handmatige bediening mogelijk)"
            return self._state_dict()

        if strategy == STRATEGY_UPS:
            await self._stop_battery()
            self._last_action = "ups"
            self._last_reason = "UPS stand-by — batterij behoudt lading, geen export"
            return self._state_dict()

        if strategy in PID_STRATEGIES:
            # PID strategies are driven by the 15s timer, not this 60s loop.
            # Just return cached state so sensors stay alive.
            return self._state_dict()

        if strategy == STRATEGY_ARBITRAGE:
            await self._run_arbitrage()
            return self._state_dict()

        if strategy == STRATEGY_CHARGE:
            await self._run_charge()
            return self._state_dict()

        if strategy == STRATEGY_SELL:
            await self._run_sell()
            return self._state_dict()

        if strategy == STRATEGY_TIMED:
            await self._run_timed()
            return self._state_dict()

        return self._state_dict()

    # ------------------------------------------------------------------
    # Strategy runners
    # ------------------------------------------------------------------

    async def _run_arbitrage(self) -> None:
        price_sensor = self._get_conf(CONF_PRICE_SENSOR, None)
        forecast_attr = self._get_conf(CONF_PRICE_FORECAST_ATTR, DEFAULT_PRICE_FORECAST_ATTR)
        n_cheap = int(self._get_conf(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS))
        n_expensive = int(self._get_conf(CONF_EXPENSIVE_HOURS, DEFAULT_EXPENSIVE_HOURS))
        min_soc = float(self._get_conf(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        max_soc = float(self._get_conf(CONF_MAX_SOC, DEFAULT_MAX_SOC))
        charge_power = int(self._get_conf(CONF_CHARGE_POWER, DEFAULT_CHARGE_POWER))
        discharge_power = int(self._get_conf(CONF_DISCHARGE_POWER, DEFAULT_DISCHARGE_POWER))
        min_price_delta = float(self._get_conf(CONF_MIN_PRICE_DELTA, DEFAULT_MIN_PRICE_DELTA))

        # Safety: any failure below must park the battery at 0W before returning.
        # Leaving the previous command in place (the old behaviour) means a stale
        # charge/discharge command can keep running indefinitely while HA shows
        # an error state and nobody is the wiser.
        if not price_sensor:
            await self._stop_battery()
            self._last_action = "config_error"
            self._last_reason = "Geen prijs-sensor geconfigureerd — batterij gestopt"
            return

        price_state = self.hass.states.get(price_sensor)
        if price_state is None:
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = f"Prijssensor {price_sensor} niet gevonden — batterij gestopt"
            return

        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            # Without a real SOC reading we cannot verify min_soc/max_soc, so we
            # cannot safely issue a new charge/discharge command either — stop
            # instead of silently assuming 50%.
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = SOC_UNAVAILABLE_REASON
            return

        forecast_raw = price_state.attributes.get(forecast_attr, [])
        decision = parse_zonneplan_forecast(
            forecast_raw, n_cheap, n_expensive, min_price_delta,
            current_soc=current_soc, min_soc=min_soc, max_soc=max_soc,
            charge_power_w=charge_power, discharge_power_w=discharge_power,
        )
        self._last_decision = decision
        self._last_plan = decision.plan if decision else None

        if decision is None:
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = "Geen geldige prijsdata beschikbaar — batterij gestopt"
            return

        _LOGGER.debug(
            "Arbitrage tick: SOC=%.1f%%, decision.action=%s, reason=%s, plan_profitable=%s",
            current_soc, decision.action, decision.reason,
            decision.plan.is_profitable if decision.plan else None,
        )
        _diag_write(
            self.config_entry.entry_id,
            f"TICK SOC={current_soc:.1f}% action={decision.action} reason={decision.reason!r}",
        )

        # Get current electricity price for charge cost tracking.
        # NOTE: price_state.state is already in EUR/kWh (the sensor's native
        # unit of measurement) — unlike the raw nano-euro encoding used inside
        # the forecast[] attribute items, which is what PRICE_UNIT (1e7) is
        # for. Dividing .state by PRICE_UNIT here was a bug: it silently
        # shrank every profit figure to ~0, since a real price like 0.25
        # became 0.000000025. Confirmed against dashboard.yaml, which plots
        # .state directly (no division) on the same axis as forecast values
        # that ARE divided by PRICE_UNIT.
        current_price_eur = None
        try:
            current_price_eur = float(price_state.state)
        except (ValueError, TypeError):
            pass

        # Daily profit reset at midnight
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._today_date:
            self._total_profit += self._today_discharge_revenue - self._today_charge_cost
            self._today_charge_cost = 0.0
            self._today_discharge_revenue = 0.0
            self._today_charge_kwh = 0.0
            self._today_discharge_kwh = 0.0
            self._today_date = today

        # Pre-action: if a charge OR discharge slot starts within pre_charge_minutes,
        # start that action now rather than waiting for the exact slot boundary.
        #
        # Stability checks to prevent rapid flipping when DP plan wavers near boundaries:
        # (B) FLIP cooldown — if we recently commanded the OPPOSITE direction, skip
        # (D) Don't pre-action against a direction we only just switched to
        pre_minutes = int(self._get_conf(CONF_PRE_CHARGE_MINUTES, DEFAULT_PRE_CHARGE_MINUTES))
        if pre_minutes > 0 and decision.action == "idle" and decision.plan:
            now_utc = datetime.now(timezone.utc)
            next_event = None
            next_event_action = None
            if decision.plan.next_charge:
                next_event = decision.plan.next_charge
                next_event_action = "charge"
            if decision.plan.next_discharge and (
                next_event is None or decision.plan.next_discharge.dt < next_event.dt
            ):
                next_event = decision.plan.next_discharge
                next_event_action = "discharge"

            if next_event:
                seconds_to_event = (next_event.dt - now_utc).total_seconds()
                if 0 < seconds_to_event <= pre_minutes * 60:
                    # Cooldown: skip pre-action if we recently did the OPPOSITE direction.
                    # Uses _last_active_* which survives standby ticks.
                    opposite = "discharge" if next_event_action == "charge" else "charge"
                    cooldown_active = False
                    if (
                        self._last_active_direction == opposite
                        and self._last_active_at is not None
                    ):
                        elapsed = time.monotonic() - self._last_active_at
                        if elapsed < DEFAULT_PRE_ACTION_FLIP_COOLDOWN:
                            cooldown_active = True
                            _LOGGER.info(
                                "Pre-action %s OVERGESLAGEN — laatste %s %.0fs geleden (cooldown %ds)",
                                next_event_action, opposite, elapsed, DEFAULT_PRE_ACTION_FLIP_COOLDOWN,
                            )
                            _diag_write(
                                self.config_entry.entry_id,
                                f"PREACTION_SKIP target={next_event_action} "
                                f"last_active={opposite} elapsed={elapsed:.0f}s",
                            )

                    if not cooldown_active:
                        minutes_left = seconds_to_event / 60
                        decision.action = next_event_action
                        label = "Voorlaad" if next_event_action == "charge" else "Vooronladen"
                        slot_label = "laaduur" if next_event_action == "charge" else "ontlaaduur"
                        decision.reason = (
                            f"{label} — over {minutes_left:.0f} min start gepland {slot_label} "
                            f"(€{next_event.price_eur:.3f}/kWh)"
                        )

        # SOC top-up/bottom-out hysteresis (thermostat-style deadband). Prevents DP
        # from re-triggering "charge" for every tiny SOC dip right at max_soc (or
        # "discharge" for every tiny uptick right at min_soc) — a self-inflicted
        # oscillation that a fixed max_soc/min_soc threshold alone can't avoid,
        # since DP re-optimises from the actual current SOC on every single tick.
        if current_soc >= max_soc:
            self._topped_up = True
        elif current_soc <= max_soc - DEFAULT_SOC_TOP_UP_DEADBAND:
            self._topped_up = False

        if current_soc <= min_soc:
            self._bottomed_out = True
        elif current_soc >= min_soc + DEFAULT_SOC_TOP_UP_DEADBAND:
            self._bottomed_out = False

        if self._topped_up and decision.action == "charge":
            _diag_write(
                self.config_entry.entry_id,
                f"TOPPED_UP SOC={current_soc:.1f}% >= max {max_soc:.0f}% — "
                f"laden geblokkeerd tot < {max_soc - DEFAULT_SOC_TOP_UP_DEADBAND:.0f}%",
            )
            decision.action = "idle"
            decision.reason = (
                f"Vol — SOC {current_soc:.0f}% wacht tot onder "
                f"{max_soc - DEFAULT_SOC_TOP_UP_DEADBAND:.0f}% voor nieuw laadmoment"
            )
        elif self._bottomed_out and decision.action == "discharge":
            _diag_write(
                self.config_entry.entry_id,
                f"BOTTOMED_OUT SOC={current_soc:.1f}% <= min {min_soc:.0f}% — "
                f"ontladen geblokkeerd tot > {min_soc + DEFAULT_SOC_TOP_UP_DEADBAND:.0f}%",
            )
            decision.action = "idle"
            decision.reason = (
                f"Leeg — SOC {current_soc:.0f}% wacht tot boven "
                f"{min_soc + DEFAULT_SOC_TOP_UP_DEADBAND:.0f}% voor nieuw ontlaadmoment"
            )

        # Post-decision anti-flip: two protections working together.
        #
        # (a) Minimum action duration: once we start charge/discharge, commit for
        #     at least MIN_ACTION_SECONDS. If DP wants to switch to the opposite,
        #     keep going instead — unless a SOC limit blocks it.
        #
        # (b) Cooldown on flips (existing): even beyond minimum duration, block a
        #     direct reversal within the cooldown window.
        if decision.action in ("charge", "discharge") and self._last_active_direction is not None:
            opposite = "discharge" if decision.action == "charge" else "charge"
            if self._last_active_direction == opposite and self._last_active_at is not None:
                elapsed = time.monotonic() - self._last_active_at
                sticky_action = self._last_active_direction  # keep doing this

                # Can we still do sticky? (SOC limits)
                soc_blocks_sticky = (
                    (sticky_action == "charge" and current_soc >= max_soc) or
                    (sticky_action == "discharge" and current_soc <= min_soc)
                )

                # Subtract a small safety margin so scheduler jitter right at the exact
                # boundary can't let a flip slip through un-blocked (two coordinator
                # instances starting their action ~1s apart can otherwise land on opposite
                # sides of the threshold at the same tick — one gets stuck, one flips).
                if elapsed < DEFAULT_MIN_ACTION_SECONDS - DEFAULT_STICKY_SAFETY_MARGIN and not soc_blocks_sticky:
                    # Keep doing the previous action — don't flip yet
                    _LOGGER.info(
                        "Anti-flip: houd %s vast (%.0fs / %ds minimum), DP wou %s",
                        sticky_action, elapsed, DEFAULT_MIN_ACTION_SECONDS, decision.action,
                    )
                    _diag_write(
                        self.config_entry.entry_id,
                        f"STICKY keep={sticky_action} elapsed={elapsed:.0f}s "
                        f"min={DEFAULT_MIN_ACTION_SECONDS}s (DP wanted {decision.action})",
                    )
                    decision.action = sticky_action
                    decision.reason = (
                        f"Anti-flip: {sticky_action} vasthouden ({elapsed:.0f}s / "
                        f"{DEFAULT_MIN_ACTION_SECONDS}s minimum)"
                    )
                elif elapsed < DEFAULT_PRE_ACTION_FLIP_COOLDOWN and soc_blocks_sticky:
                    # Sticky would violate SOC — fall to idle, don't flip immediately
                    _LOGGER.info(
                        "Anti-flip: %s→%s geblokkeerd (SOC-limiet), blijf idle",
                        sticky_action, decision.action,
                    )
                    _diag_write(
                        self.config_entry.entry_id,
                        f"ACTION_BLOCK target={decision.action} last_active={sticky_action} "
                        f"SOC-limit → idle",
                    )
                    decision.action = "idle"
                    decision.reason = f"Cooldown: {sticky_action} klaar, wacht voor {decision.action}"

        if should_charge(decision, current_soc, max_soc):
            _LOGGER.info("Arbitrage LADEN — SOC=%.1f%%, %s", current_soc, decision.reason)
            if await self._set_battery_power(-charge_power):
                self._last_action = "charging"
                if current_price_eur is not None:
                    kwh_this_tick = charge_power / 1000 * (UPDATE_INTERVAL / 3600)
                    self._today_charge_cost += current_price_eur * kwh_this_tick
                    self._today_charge_kwh += kwh_this_tick
            else:
                self._last_action = "error"
                decision.reason = f"Modbus-commando geweigerd — {decision.reason}"
        elif should_discharge(decision, current_soc, min_soc):
            _LOGGER.info("Arbitrage ONTLADEN — SOC=%.1f%%, %s", current_soc, decision.reason)
            if await self._set_battery_power(discharge_power):
                self._last_action = "discharging"
                if current_price_eur is not None:
                    kwh_this_tick = discharge_power / 1000 * (UPDATE_INTERVAL / 3600)
                    self._today_discharge_revenue += current_price_eur * kwh_this_tick
                    self._today_discharge_kwh += kwh_this_tick
            else:
                self._last_action = "error"
                decision.reason = f"Modbus-commando geweigerd — {decision.reason}"
        else:
            if decision.action == "charge" and current_soc >= max_soc:
                _LOGGER.info("Arbitrage wou laden maar SOC=%.1f%% >= max %.1f%%", current_soc, max_soc)
            elif decision.action == "discharge" and current_soc <= min_soc:
                _LOGGER.info("Arbitrage wou ontladen maar SOC=%.1f%% <= min %.1f%%", current_soc, min_soc)
            await self._stop_battery()
            self._last_action = decision.action

        self._last_reason = decision.reason

    async def _run_self_consumption(self) -> None:
        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            # Without a real SOC reading we cannot verify min_soc/max_soc —
            # stop instead of silently assuming 50% and issuing a new command.
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = SOC_UNAVAILABLE_REASON
            return

        grid_power = await self._get_grid_power()
        if grid_power is None:
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = "Netwerk vermogen sensor niet beschikbaar — batterij gestopt"
            return

        if self._pid_controller is None:
            self._rebuild_pid_controller()

        min_soc = float(self._get_conf(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        max_soc = float(self._get_conf(CONF_MAX_SOC, DEFAULT_MAX_SOC))
        target = float(self._get_conf(CONF_TARGET_GRID_POWER, DEFAULT_TARGET_GRID_POWER))

        decision = compute_self_consumption(
            grid_power_w=grid_power,
            target_grid_power_w=target,
            pid=self._pid_controller,
            pid_state=self._pid_state,
            current_soc=current_soc,
            min_soc=min_soc,
            max_soc=max_soc,
        )
        self._pid_state = decision.pid_state
        self._last_pid_decision = decision

        if decision.power_w == 0:
            await self._stop_battery()
            self._last_action = "idle"
        elif decision.power_w < 0:
            if await self._set_battery_power(decision.power_w):
                self._last_action = "charging"
            else:
                self._last_action = "error"
                decision.reason = f"Modbus-commando geweigerd — {decision.reason}"
        else:
            if await self._set_battery_power(decision.power_w):
                self._last_action = "discharging"
            else:
                self._last_action = "error"
                decision.reason = f"Modbus-commando geweigerd — {decision.reason}"

        self._last_reason = decision.reason

    async def _run_charge_pv(self) -> None:
        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            # Without a real SOC reading we cannot verify min_soc/max_soc —
            # stop instead of silently assuming 50% and issuing a new command.
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = SOC_UNAVAILABLE_REASON
            return

        grid_power = await self._get_grid_power()
        if grid_power is None:
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = "Netwerk vermogen sensor niet beschikbaar — batterij gestopt"
            return

        if self._pid_controller is None:
            self._rebuild_pid_controller()

        min_soc = float(self._get_conf(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        max_soc = float(self._get_conf(CONF_MAX_SOC, DEFAULT_MAX_SOC))
        target = float(self._get_conf(CONF_TARGET_GRID_POWER, DEFAULT_TARGET_GRID_POWER))
        peak_limit = float(self._get_conf(CONF_PEAK_SHAVE_LIMIT, DEFAULT_PEAK_SHAVE_LIMIT))
        discharge_power = int(self._get_conf(CONF_DISCHARGE_POWER, DEFAULT_DISCHARGE_POWER))

        decision = compute_charge_pv(
            grid_power_w=grid_power,
            target_grid_power_w=target,
            peak_shave_limit_w=peak_limit,
            pid=self._pid_controller,
            pid_state=self._pid_state,
            current_soc=current_soc,
            min_soc=min_soc,
            max_soc=max_soc,
            max_discharge_power_w=discharge_power,
        )
        self._pid_state = decision.pid_state
        self._last_pid_decision = decision

        if decision.power_w == 0:
            await self._stop_battery()
            self._last_action = "idle"
        elif decision.power_w < 0:
            if await self._set_battery_power(decision.power_w):
                self._last_action = "charging"
            else:
                self._last_action = "error"
                decision.reason = f"Modbus-commando geweigerd — {decision.reason}"
        else:
            if await self._set_battery_power(decision.power_w):
                self._last_action = "discharging"
            else:
                self._last_action = "error"
                decision.reason = f"Modbus-commando geweigerd — {decision.reason}"

        self._last_reason = decision.reason

    async def _run_charge(self) -> None:
        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = SOC_UNAVAILABLE_REASON
            return

        target_soc = float(self._get_conf(CONF_TARGET_SOC, DEFAULT_TARGET_SOC))
        max_soc = float(self._get_conf(CONF_MAX_SOC, DEFAULT_MAX_SOC))
        charge_power = int(self._get_conf(CONF_CHARGE_POWER, DEFAULT_CHARGE_POWER))

        decision = compute_charge_to_target(current_soc, target_soc, max_soc, charge_power)

        if decision.action == "charge":
            if await self._set_battery_power(-charge_power):
                self._last_action = "charging"
            else:
                self._last_action = "error"
                decision.reason = f"Modbus-commando geweigerd — {decision.reason}"
        else:
            await self._stop_battery()
            self._last_action = "idle"

        self._last_reason = decision.reason

    async def _run_sell(self) -> None:
        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            await self._stop_battery()
            self._last_action = "error"
            self._last_reason = SOC_UNAVAILABLE_REASON
            return

        target_soc = float(self._get_conf(CONF_TARGET_SOC, DEFAULT_TARGET_SOC))
        min_soc = float(self._get_conf(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        discharge_power = int(self._get_conf(CONF_DISCHARGE_POWER, DEFAULT_DISCHARGE_POWER))

        decision = compute_sell_to_target(current_soc, target_soc, min_soc, discharge_power)

        if decision.action == "discharge":
            if await self._set_battery_power(discharge_power):
                self._last_action = "discharging"
            else:
                self._last_action = "error"
                decision.reason = f"Modbus-commando geweigerd — {decision.reason}"
        else:
            await self._stop_battery()
            self._last_action = "idle"

        self._last_reason = decision.reason

    async def _run_timed(self) -> None:
        periods = []

        # Period A (always active)
        start_a = self._get_conf(CONF_TIMED_PERIOD_A_START, DEFAULT_TIMED_PERIOD_A_START)
        end_a = self._get_conf(CONF_TIMED_PERIOD_A_END, DEFAULT_TIMED_PERIOD_A_END)
        strat_a = self._get_conf(CONF_TIMED_PERIOD_A_STRATEGY, DEFAULT_TIMED_PERIOD_A_STRATEGY)
        if start_a and end_a:
            periods.append(TimedPeriod(label="A", start=start_a, end=end_a, sub_strategy=strat_a))

        # Period B (optional)
        if self._get_conf(CONF_TIMED_PERIOD_B_ENABLED, False):
            start_b = self._get_conf(CONF_TIMED_PERIOD_B_START, None)
            end_b = self._get_conf(CONF_TIMED_PERIOD_B_END, None)
            strat_b = self._get_conf(CONF_TIMED_PERIOD_B_STRATEGY, STRATEGY_OFF)
            if start_b and end_b:
                periods.append(TimedPeriod(label="B", start=start_b, end=end_b, sub_strategy=strat_b))

        # Period C (optional)
        if self._get_conf(CONF_TIMED_PERIOD_C_ENABLED, False):
            start_c = self._get_conf(CONF_TIMED_PERIOD_C_START, None)
            end_c = self._get_conf(CONF_TIMED_PERIOD_C_END, None)
            strat_c = self._get_conf(CONF_TIMED_PERIOD_C_STRATEGY, STRATEGY_OFF)
            if start_c and end_c:
                periods.append(TimedPeriod(label="C", start=start_c, end=end_c, sub_strategy=strat_c))

        sub_strategy, active_label = resolve_timed_strategy(periods, STRATEGY_OFF)
        self._timed_active_period = f"{active_label} ({sub_strategy})"

        _LOGGER.debug("Timed strategie actief blok: %s → %s", active_label, sub_strategy)
        await self._dispatch_sub_strategy(sub_strategy)

    async def _dispatch_sub_strategy(self, sub_strategy: str) -> None:
        """Dispatch to a sub-strategy runner (used by timed strategy)."""
        runners = {
            STRATEGY_ARBITRAGE: self._run_arbitrage,
            STRATEGY_SELF_CONSUMPTION: self._run_self_consumption,
            STRATEGY_CHARGE_PV: self._run_charge_pv,
            STRATEGY_CHARGE: self._run_charge,
            STRATEGY_SELL: self._run_sell,
        }
        if sub_strategy in runners:
            await runners[sub_strategy]()
        elif sub_strategy == STRATEGY_UPS:
            await self._stop_battery()
            self._last_action = "ups"
            self._last_reason = "UPS stand-by (via tijdschema)"
        else:
            await self._stop_battery()
            self._last_action = "off"
            self._last_reason = "Tijdschema: geen actief blok (standaard uit)"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_conf(self, key, default):
        return self.config_entry.options.get(key, self.config_entry.data.get(key, default))

    def _find_marstek_entity(self, key: str) -> str | None:
        """Find a ViperRNMC marstek_modbus entity by its key suffix.

        ViperRNMC unique_ids have format: f"{entry_id}_{key}".
        We look up entities for the configured device_id and match on the suffix.
        """
        device_id = self._get_conf(CONF_MARSTEK_DEVICE_ID, None)
        if not device_id:
            return None

        ent_reg = er.async_get(self.hass)
        for entry in ent_reg.entities.values():
            if entry.device_id != device_id:
                continue
            if entry.platform != MARSTEK_MODBUS_DOMAIN:
                continue
            if entry.unique_id and entry.unique_id.endswith(f"_{key}"):
                return entry.entity_id
        return None

    async def _get_soc(self) -> float | None:
        entity_id = self._find_marstek_entity(KEY_BATTERY_SOC)
        if not entity_id:
            _LOGGER.warning("Marstek battery_soc entity niet gevonden — is ViperRNMC marstek_modbus geïnstalleerd en device_id correct?")
            return None

        state = self.hass.states.get(entity_id)
        if state and state.state not in ("unknown", "unavailable"):
            try:
                return float(state.state)
            except ValueError:
                pass
        return None

    async def _get_grid_power(self) -> float | None:
        """
        Read grid power from the configured sensor.
        Sign convention: positive = importing, negative = exporting.
        """
        sensor = self._get_conf(CONF_GRID_POWER_SENSOR, None)
        if not sensor:
            _LOGGER.warning("Geen netwerk vermogen sensor geconfigureerd")
            return None
        state = self.hass.states.get(sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _track_direction(self, target_power: int) -> None:
        """Update direction-change bookkeeping (no ramping — commands go out at full power).

        On every transition between standby/charge/discharge, records the new
        active direction and timestamp. This feeds the anti-flip hysteresis
        (sticky minimum-duration and pre-action cooldown), which still needs
        to know when a direction last changed even without power ramping.
        """
        if target_power == 0:
            new_dir = "standby"
        elif target_power < 0:
            new_dir = "charge"
        else:
            new_dir = "discharge"

        if new_dir != self._current_direction:
            self._current_direction = new_dir
            if new_dir in ("charge", "discharge"):
                self._last_active_direction = new_dir
                self._last_active_at = time.monotonic()

    async def _set_battery_power(self, power: int) -> bool:
        """Set the battery to charge/discharge at the given power via ViperRNMC Modbus.

        Convention: power < 0 = charging, power > 0 = discharging, power == 0 = standby.
        Sends commands every tick — does not check the current Modbus state.
        Commands go out at full requested power immediately (no ramp-up).

        Returns True if the command succeeded (or no write was needed because
        the Marstek was already at the target state), False if the write
        failed or the force_mode entity could not be found. Callers must
        check this before reporting "charging"/"discharging" — a rejected
        Modbus write (e.g. a power value the device refuses) must not be
        shown as if it succeeded.
        """
        self._track_direction(power)

        # Determine target. NOTE: when going to idle (power==0) we intentionally do
        # NOT try to write force_mode="stop" — some Marstek firmware/versions reject
        # that value with `not_valid_option`, causing endless error spam. Instead we
        # only zero out both power set-points. The Marstek then sits at 0W regardless
        # of what direction its force_mode still shows.
        write_force_mode = True
        if power == 0:
            target_mode = FORCE_MODE_STANDBY
            target_power = 0
            power_key = None
            write_force_mode = False  # leave force_mode alone; zero power = effectively idle
        elif power < 0:
            target_mode = FORCE_MODE_CHARGE
            target_power = abs(power)
            power_key = KEY_SET_CHARGE_POWER
        else:
            target_mode = FORCE_MODE_DISCHARGE
            target_power = power
            power_key = KEY_SET_DISCHARGE_POWER

        force_mode_entity = self._find_marstek_entity(KEY_FORCE_MODE)
        if not force_mode_entity:
            _LOGGER.warning("Marstek force_mode entity niet gevonden")
            return False

        # Diagnostic: log observed Marstek state BEFORE we write anything
        observed_force_mode = self._cached_state(force_mode_entity)
        user_mode_entity = self._find_marstek_entity(KEY_USER_WORK_MODE)
        observed_user_mode = self._cached_state(user_mode_entity) if user_mode_entity else None
        # Also capture actual power and AC measurement for diagnostic purposes
        ac_power_entity = self._find_marstek_entity("ac_power")
        battery_power_entity = self._find_marstek_entity("battery_power")
        observed_ac_power = self._cached_number(ac_power_entity) if ac_power_entity else None
        observed_battery_power = self._cached_number(battery_power_entity) if battery_power_entity else None

        _LOGGER.info(
            "Marstek observed: force_mode=%s, user_work_mode=%s | target: force_mode=%s @ %dW",
            observed_force_mode, observed_user_mode, target_mode, target_power,
        )
        _diag_write(
            self.config_entry.entry_id,
            f"OBSERVED force={observed_force_mode} usermode={observed_user_mode} "
            f"ac_power={observed_ac_power}W batt_power={observed_battery_power}W "
            f"| TARGET force={target_mode} @ {target_power}W",
        )

        try:
            written = []

            # NOTE: we intentionally do NOT touch user_work_mode anymore.
            # Marstek's anti_feed is the normal default and force_mode commands
            # override it as needed. Forcing user_work_mode=manual broke things.

            # Charge/discharge power only when not yet at target value.
            # When going to idle (power_key is None), zero out BOTH power set-points
            # so nothing lingers in the wrong direction.
            keys_to_zero = [KEY_SET_CHARGE_POWER, KEY_SET_DISCHARGE_POWER] if power_key is None else [power_key]
            for key in keys_to_zero:
                power_entity = self._find_marstek_entity(key)
                if not power_entity:
                    continue
                cached_power = self._cached_number(power_entity)
                desired = target_power if key == power_key else 0
                if cached_power != desired:
                    await self.hass.services.async_call(
                        "number", "set_value",
                        {"entity_id": power_entity, "value": float(desired)},
                        blocking=True,
                    )
                    written.append(f"{key}={desired}W (was {cached_power})")

            # Force_mode only when we want to switch it AND it's not yet at target.
            # write_force_mode=False for idle avoids the "not_valid_option" error some
            # Marstek versions throw when writing "stop".
            if write_force_mode and observed_force_mode != target_mode:
                await self.hass.services.async_call(
                    "select", "select_option",
                    {"entity_id": force_mode_entity, "option": target_mode},
                    blocking=True,
                )
                written.append(f"force_mode={target_mode} (was {observed_force_mode})")

            if written:
                _LOGGER.info("Marstek writes: %s", "; ".join(written))
                _diag_write(self.config_entry.entry_id, f"WRITE {'; '.join(written)}")
            else:
                _LOGGER.debug("Marstek %s @ %dW — al juiste state, skip writes",
                              target_mode, target_power)
                _diag_write(self.config_entry.entry_id, f"SKIP target={target_mode}@{target_power}W (already correct)")
            return True
        except Exception as exc:
            _LOGGER.exception("Fout bij aansturen Marstek via Modbus")
            _diag_write(self.config_entry.entry_id, f"ERROR {exc}")
            return False

    def _cached_state(self, entity_id: str) -> str | None:
        """Return HA's cached state for entity, or None if unavailable."""
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return state.state

    def _cached_number(self, entity_id: str) -> int | None:
        """Return HA's cached numeric state, or None if unavailable."""
        raw = self._cached_state(entity_id)
        if raw is None:
            return None
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            return None

    async def _stop_battery(self) -> bool:
        """Set battery to standby (idle) — used at idle states."""
        return await self._set_battery_power(0)



    def _state_dict(self) -> dict:
        plan = self._last_plan
        pid = self._last_pid_decision
        today = datetime.now().date()
        tomorrow = today + timedelta(days=1)

        def _fmt_plan_time(dt) -> str:
            local = dt.astimezone()
            hhmm = local.strftime("%H:%M")
            if local.date() == today:
                return hhmm
            if local.date() == tomorrow:
                return f"morgen {hhmm}"
            return local.strftime("%a %d-%m %H:%M")

        return {
            "strategy": self._strategy,
            "last_action": self._last_action,
            "last_reason": self._last_reason,
            "soc": self._last_soc,
            # Arbitrage planning
            "price_delta": round(plan.price_delta_eur, 4) if plan else None,
            "plan_profitable": plan.is_profitable if plan else None,
            "expected_profit": plan.expected_profit_eur if plan else None,
            "next_charge_start": plan.next_charge.dt.isoformat() if plan and plan.next_charge else None,
            "next_charge_end": plan.charge_window_end.isoformat() if plan and plan.charge_window_end else None,
            "next_discharge_start": plan.next_discharge.dt.isoformat() if plan and plan.next_discharge else None,
            "next_discharge_end": plan.discharge_window_end.isoformat() if plan and plan.discharge_window_end else None,
            "plan_hours": [
                {"time": _fmt_plan_time(h.dt), "action": h.action, "price": round(h.price_eur, 4), "soc": h.simulated_soc}
                for h in plan.hours
            ] if plan else [],
            "today_profit": round(self._today_discharge_revenue - self._today_charge_cost, 4),
            "today_revenue": round(self._today_discharge_revenue, 4),
            "today_cost": round(self._today_charge_cost, 4),
            "today_charge_kwh": round(self._today_charge_kwh, 2),
            "today_discharge_kwh": round(self._today_discharge_kwh, 2),
            "total_profit": round(self._total_profit + self._today_discharge_revenue - self._today_charge_cost, 4),
            # PID / self-consumption
            "pid_grid_power": round(pid.grid_power, 1) if pid else None,
            "pid_power_w": pid.power_w if pid else None,
            "pid_in_deadband": pid.in_deadband if pid else None,
            "pid_error": round(pid.error, 1) if pid else None,
            # Timed
            "timed_active_period": self._timed_active_period,
        }
