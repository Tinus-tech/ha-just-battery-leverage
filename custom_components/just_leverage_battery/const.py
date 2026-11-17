"""Constants for Just Leverage Battery."""

DOMAIN = "just_leverage_battery"

# Strategies
STRATEGY_ARBITRAGE = "arbitrage"
STRATEGY_UPS = "ups"
STRATEGY_OFF = "off"
STRATEGY_SELF_CONSUMPTION = "self_consumption"
STRATEGY_CHARGE_PV = "charge_pv"
STRATEGY_CHARGE = "charge"
STRATEGY_SELL = "sell"
STRATEGY_TIMED = "timed"

STRATEGIES = [
    STRATEGY_ARBITRAGE,
    STRATEGY_SELF_CONSUMPTION,
    STRATEGY_CHARGE_PV,
    STRATEGY_CHARGE,
    STRATEGY_SELL,
    STRATEGY_TIMED,
    STRATEGY_UPS,
    STRATEGY_OFF,
]

# Sub-strategies available in the timed scheduler
TIMED_SUB_STRATEGIES = [
    STRATEGY_ARBITRAGE,
    STRATEGY_SELF_CONSUMPTION,
    STRATEGY_CHARGE_PV,
    STRATEGY_CHARGE,
    STRATEGY_SELL,
    STRATEGY_UPS,
    STRATEGY_OFF,
]

# Control backend — which integration actually talks to the Marstek battery.
# Both backends use the SAME CONF_MARSTEK_DEVICE_ID (the HA device, not an
# entity) — Settings > Devices & Services > pick the Marstek device > copy
# Device ID works identically for either.
CONF_CONTROL_BACKEND = "control_backend"
BACKEND_MODBUS = "modbus"
BACKEND_LOCAL_API = "local_api"
CONTROL_BACKENDS = [BACKEND_MODBUS, BACKEND_LOCAL_API]
DEFAULT_CONTROL_BACKEND = BACKEND_MODBUS  # preserves existing installs' behaviour

# Config keys — common
CONF_MARSTEK_DEVICE_ID = "marstek_device_id"
CONF_MIN_SOC = "min_soc"
CONF_MAX_SOC = "max_soc"
CONF_CHARGE_POWER = "charge_power"
CONF_DISCHARGE_POWER = "discharge_power"
CONF_PRE_CHARGE_MINUTES = "pre_charge_minutes"
CONF_STRATEGY = "strategy"

# Config keys — arbitrage
CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_FORECAST_ATTR = "price_forecast_attr"
CONF_CHEAP_HOURS = "cheap_hours"
CONF_EXPENSIVE_HOURS = "expensive_hours"
CONF_MIN_PRICE_DELTA = "min_price_delta"

# Config keys — self_consumption / charge_pv (PID)
CONF_GRID_POWER_SENSOR = "grid_power_sensor"
CONF_TARGET_GRID_POWER = "target_grid_power"
CONF_PID_KP = "pid_kp"
CONF_PID_KI = "pid_ki"
CONF_PID_KD = "pid_kd"
CONF_PID_DEADBAND = "pid_deadband"
CONF_PEAK_SHAVE_LIMIT = "peak_shave_limit"

# Config keys — charge / sell
CONF_TARGET_SOC = "target_soc"

# Config keys — timed (3 time periods A, B, C)
CONF_TIMED_PERIOD_A_START = "timed_period_a_start"
CONF_TIMED_PERIOD_A_END = "timed_period_a_end"
CONF_TIMED_PERIOD_A_STRATEGY = "timed_period_a_strategy"
CONF_TIMED_PERIOD_B_ENABLED = "timed_period_b_enabled"
CONF_TIMED_PERIOD_B_START = "timed_period_b_start"
CONF_TIMED_PERIOD_B_END = "timed_period_b_end"
CONF_TIMED_PERIOD_B_STRATEGY = "timed_period_b_strategy"
CONF_TIMED_PERIOD_C_ENABLED = "timed_period_c_enabled"
CONF_TIMED_PERIOD_C_START = "timed_period_c_start"
CONF_TIMED_PERIOD_C_END = "timed_period_c_end"
CONF_TIMED_PERIOD_C_STRATEGY = "timed_period_c_strategy"

# Defaults — common
DEFAULT_MIN_SOC = 10
DEFAULT_MAX_SOC = 95
DEFAULT_CHARGE_POWER = 2000
DEFAULT_DISCHARGE_POWER = 2000
DEFAULT_PRE_CHARGE_MINUTES = 15  # start charging this many minutes before next planned charge slot
                                  # (was 60; with ramp-up removed there's no more gradual power
                                  # build-up to compensate for — this is just margin for tick
                                  # granularity. Combined with the price check below.)
DEFAULT_PRE_ACTION_PRICE_TOLERANCE = 0.03  # EUR/kWh: pre-action only fires if the current price
                                            # isn't worse than the target slot's price by more
                                            # than this much
DEFAULT_PRE_ACTION_FLIP_COOLDOWN = 300  # 5 min: skip pre-action if we did the opposite direction this recently
DEFAULT_MIN_ACTION_SECONDS = 900  # 15 min: once charge/discharge started, commit for at least this long
DEFAULT_STICKY_SAFETY_MARGIN = 5  # seconds subtracted from the sticky check so scheduler jitter
                                   # right at the boundary can't let a flip slip through un-blocked
DEFAULT_SOC_TOP_UP_DEADBAND = 3   # %-points: once SOC hits max_soc, block further charging until
                                   # SOC drops this many points below max_soc (thermostat-style deadband)

# Defaults — arbitrage
DEFAULT_CHEAP_HOURS = 4
DEFAULT_EXPENSIVE_HOURS = 4
DEFAULT_MIN_PRICE_DELTA = 0.06
DEFAULT_PRICE_FORECAST_ATTR = "forecast"
DEFAULT_STRATEGY = STRATEGY_OFF

# Defaults — PID
DEFAULT_TARGET_GRID_POWER = 0
# PID gains for self-consumption / charge_pv. Lowered from kp=1.0/ki=0.05 after
# simulation showed the original values diverge (unbounded oscillation toward
# the output clamp) even for a SINGLE battery against a constant PV surplus —
# not just when multiple instances share one grid meter. See also
# DEFAULT_PID_MAX_STEP_W, which bounds any residual oscillation regardless of
# gain tuning.
DEFAULT_PID_KP = 0.3
DEFAULT_PID_KI = 0.03
DEFAULT_PID_KD = 0.01
DEFAULT_PID_DEADBAND = 15
DEFAULT_PID_MAX_STEP_W = 300  # max change in commanded power per PID tick (rate limiter)
DEFAULT_PEAK_SHAVE_LIMIT = 2500

# Defaults — charge/sell
DEFAULT_TARGET_SOC = 80

# Defaults — timed
DEFAULT_TIMED_PERIOD_A_START = "23:00:00"
DEFAULT_TIMED_PERIOD_A_END = "07:00:00"
DEFAULT_TIMED_PERIOD_A_STRATEGY = STRATEGY_ARBITRAGE

# ViperRNMC Marstek Modbus integration entity keys
# These are the suffixes of the unique_id that ViperRNMC uses for its entities.
# Unique_id format: f"{entry_id}_{key}"
MARSTEK_MODBUS_DOMAIN = "marstek_modbus"
KEY_BATTERY_SOC = "battery_soc"
KEY_FORCE_MODE = "force_mode"
KEY_USER_WORK_MODE = "user_work_mode"
KEY_RS485_CONTROL_MODE = "rs485_control_mode"
KEY_SET_CHARGE_POWER = "set_charge_power"
KEY_SET_DISCHARGE_POWER = "set_discharge_power"

# force_mode select options (ViperRNMC marstek_modbus values)
FORCE_MODE_STANDBY = "stop"
FORCE_MODE_CHARGE = "charge"
FORCE_MODE_DISCHARGE = "discharge"

# user_work_mode must be "manual" for force_mode writes to take effect.
# In "anti_feed" or "trade_mode" the Marstek overrides our force_mode commands,
# causing oscillation (battery flips between commanded action and anti-feed default).
USER_WORK_MODE_MANUAL = "manual"

# taurgis/has-marstek-local-api integration (UDP-based local API, no Modbus
# gateway needed). HA domain is "marstek" — note this is NOT the same as
# ViperRNMC's "marstek_modbus" domain above; the two integrations can be
# installed side by side without conflict since they use different domains.
MARSTEK_LOCAL_API_DOMAIN = "marstek"
LOCAL_API_SERVICE_SET_PASSIVE_MODE = "set_passive_mode"
LOCAL_API_ATTR_DEVICE_ID = "device_id"
LOCAL_API_ATTR_POWER = "power"
LOCAL_API_ATTR_DURATION = "duration"
# Passive-mode commands auto-expire after this many seconds unless refreshed.
# Set well above our own tick interval (60s) so normal operation never lapses,
# but short enough to act as a dead-man's switch if this integration stops
# ticking altogether (HA crash, etc.) — the battery falls back to its own
# default behaviour instead of being stuck on a stale command forever, which
# is actually safer than the Modbus backend's "persists until overwritten".
DEFAULT_LOCAL_API_COMMAND_DURATION_S = 300

# Send-throttling for the local API: the Marstek's local UDP API is known to
# become unstable (dropped CT002 link, unresponsive) under frequent/bursty
# requests, and the taurgis integration's own docs recommend at most one
# request per update interval. We only resend set_passive_mode when the
# commanded power actually changes by more than this many watts, OR when this
# many seconds have passed since the last send (a keep-alive refresh so the
# command doesn't hit DEFAULT_LOCAL_API_COMMAND_DURATION_S and auto-expire).
# The keep-alive interval is kept well below the command duration so a skipped
# tick never risks the battery falling back to its own default behaviour.
LOCAL_API_POWER_CHANGE_THRESHOLD_W = 50
LOCAL_API_KEEPALIVE_INTERVAL_S = 120

# Update intervals
UPDATE_INTERVAL = 60           # seconds — main coordinator
PID_UPDATE_INTERVAL = 15       # seconds — PID tick (self_consumption, charge_pv)
