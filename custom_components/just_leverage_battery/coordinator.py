"""DataUpdateCoordinator for Just Leverage Battery."""
from __future__ import annotations

import logging
from datetime import timedelta

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
    KEY_RS485_CONTROL_MODE,
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
        self._power_slider = None  # set by number.py after platform setup
        self._last_power_command: int | None = None  # laatste power waarde — voorkomt onnodige Modbus writes
        self._last_force_mode: str | None = None  # laatste force_mode — voorkomt onnodige Modbus writes

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
        # Opheven handmatige override bij strategiewissel
        if self._power_slider and self._power_slider.manual_override:
            self._power_slider.clear_manual_override()
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
        if self._power_slider and self._power_slider.manual_override:
            return  # handmatige besturing heeft voorrang

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
        # Check manual override
        if self._power_slider and self._power_slider.manual_override:
            self._last_action = "handmatig"
            self._last_reason = f"Handmatige besturing actief ({int(self._power_slider.native_value)}W)"
            return self._state_dict()

        strategy = self._strategy

        if strategy == STRATEGY_OFF:
            await self._stop_battery()
            self._last_action = "off"
            self._last_reason = "Strategie staat op Uit"
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

        if not price_sensor:
            self._last_action = "config_error"
            self._last_reason = "Geen prijs-sensor geconfigureerd"
            return

        price_state = self.hass.states.get(price_sensor)
        if price_state is None:
            self._last_action = "error"
            self._last_reason = f"Prijssensor {price_sensor} niet gevonden"
            return

        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            current_soc = 50.0

        forecast_raw = price_state.attributes.get(forecast_attr, [])
        decision = parse_zonneplan_forecast(
            forecast_raw, n_cheap, n_expensive, min_price_delta,
            current_soc=current_soc, min_soc=min_soc, max_soc=max_soc,
            charge_power_w=charge_power, discharge_power_w=discharge_power,
        )
        self._last_decision = decision
        self._last_plan = decision.plan if decision else None

        if decision is None:
            self._last_action = "error"
            self._last_reason = "Geen geldige prijsdata beschikbaar"
            return

        if should_charge(decision, current_soc, max_soc):
            await self._set_battery_power(-charge_power)
            self._last_action = "charging"
        elif should_discharge(decision, current_soc, min_soc):
            await self._set_battery_power(discharge_power)
            self._last_action = "discharging"
        else:
            await self._stop_battery()
            self._last_action = decision.action

        self._last_reason = decision.reason

    async def _run_self_consumption(self) -> None:
        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            current_soc = 50.0

        grid_power = await self._get_grid_power()
        if grid_power is None:
            self._last_action = "error"
            self._last_reason = "Netwerk vermogen sensor niet beschikbaar"
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
            await self._set_battery_power(decision.power_w)
            self._last_action = "charging"
        else:
            await self._set_battery_power(decision.power_w)
            self._last_action = "discharging"

        self._last_reason = decision.reason

    async def _run_charge_pv(self) -> None:
        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            current_soc = 50.0

        grid_power = await self._get_grid_power()
        if grid_power is None:
            self._last_action = "error"
            self._last_reason = "Netwerk vermogen sensor niet beschikbaar"
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
            await self._set_battery_power(decision.power_w)
            self._last_action = "charging"
        else:
            await self._set_battery_power(decision.power_w)
            self._last_action = "discharging"

        self._last_reason = decision.reason

    async def _run_charge(self) -> None:
        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            self._last_action = "error"
            self._last_reason = "SOC niet beschikbaar"
            return

        target_soc = float(self._get_conf(CONF_TARGET_SOC, DEFAULT_TARGET_SOC))
        max_soc = float(self._get_conf(CONF_MAX_SOC, DEFAULT_MAX_SOC))
        charge_power = int(self._get_conf(CONF_CHARGE_POWER, DEFAULT_CHARGE_POWER))

        decision = compute_charge_to_target(current_soc, target_soc, max_soc, charge_power)

        if decision.action == "charge":
            await self._set_battery_power(-charge_power)
            self._last_action = "charging"
        else:
            await self._stop_battery()
            self._last_action = "idle"

        self._last_reason = decision.reason

    async def _run_sell(self) -> None:
        current_soc = await self._get_soc()
        self._last_soc = current_soc
        if current_soc is None:
            self._last_action = "error"
            self._last_reason = "SOC niet beschikbaar"
            return

        target_soc = float(self._get_conf(CONF_TARGET_SOC, DEFAULT_TARGET_SOC))
        min_soc = float(self._get_conf(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        discharge_power = int(self._get_conf(CONF_DISCHARGE_POWER, DEFAULT_DISCHARGE_POWER))

        decision = compute_sell_to_target(current_soc, target_soc, min_soc, discharge_power)

        if decision.action == "discharge":
            await self._set_battery_power(discharge_power)
            self._last_action = "discharging"
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

    async def _set_battery_power(self, power: int) -> None:
        """Set the battery to charge/discharge at the given power via ViperRNMC Modbus.

        Convention: power < 0 = charging, power > 0 = discharging, power == 0 = standby.
        Only writes Modbus registers when the value actually changes (no flickering).
        """
        # Determine target force_mode and power magnitude
        if power == 0:
            target_mode = FORCE_MODE_STANDBY
            target_power = 0
            power_key = None
        elif power < 0:
            target_mode = FORCE_MODE_CHARGE
            target_power = abs(power)
            power_key = KEY_SET_CHARGE_POWER
        else:
            target_mode = FORCE_MODE_DISCHARGE
            target_power = power
            power_key = KEY_SET_DISCHARGE_POWER

        # Skip if nothing changed
        if power == self._last_power_command and target_mode == self._last_force_mode:
            return

        force_mode_entity = self._find_marstek_entity(KEY_FORCE_MODE)
        if not force_mode_entity:
            _LOGGER.warning("Marstek force_mode entity niet gevonden")
            return

        try:
            # First set the power value (only if charging or discharging)
            if power_key is not None:
                power_entity = self._find_marstek_entity(power_key)
                if power_entity:
                    await self.hass.services.async_call(
                        "number",
                        "set_value",
                        {"entity_id": power_entity, "value": float(target_power)},
                        blocking=True,
                    )

            # Then set the force_mode (only if it changed)
            if target_mode != self._last_force_mode:
                await self.hass.services.async_call(
                    "select",
                    "select_option",
                    {"entity_id": force_mode_entity, "option": target_mode},
                    blocking=True,
                )

            self._last_power_command = power
            self._last_force_mode = target_mode
            _LOGGER.info("Marstek %s @ %dW", target_mode, target_power)

            # Update slider zodat de gebruiker ziet wat de coordinator stuurt
            if self._power_slider:
                self._power_slider.set_coordinator_value(power)
        except Exception as exc:
            _LOGGER.error("Fout bij aansturen Marstek via Modbus: %s", exc)

    async def _stop_battery(self) -> None:
        """Set battery to standby (idle) — used at idle states."""
        await self._set_battery_power(0)



    def _state_dict(self) -> dict:
        plan = self._last_plan
        pid = self._last_pid_decision
        return {
            "strategy": self._strategy,
            "last_action": self._last_action,
            "last_reason": self._last_reason,
            "soc": self._last_soc,
            # Arbitrage planning
            "price_delta": round(plan.price_delta_eur, 4) if plan else None,
            "plan_profitable": plan.is_profitable if plan else None,
            "next_charge_start": plan.next_charge.dt.isoformat() if plan and plan.next_charge else None,
            "next_charge_end": plan.charge_window_end.isoformat() if plan and plan.charge_window_end else None,
            "next_discharge_start": plan.next_discharge.dt.isoformat() if plan and plan.next_discharge else None,
            "next_discharge_end": plan.discharge_window_end.isoformat() if plan and plan.discharge_window_end else None,
            "plan_hours": [
                {"time": h.dt.strftime("%H:%M"), "action": h.action, "price": round(h.price_eur, 4), "soc": h.simulated_soc}
                for h in plan.hours
            ] if plan else [],
            # PID / self-consumption
            "pid_grid_power": round(pid.grid_power, 1) if pid else None,
            "pid_power_w": pid.power_w if pid else None,
            "pid_in_deadband": pid.in_deadband if pid else None,
            "pid_error": round(pid.error, 1) if pid else None,
            # Timed
            "timed_active_period": self._timed_active_period,
        }
