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

# Config keys — common
CONF_MARSTEK_DEVICE_ID = "marstek_device_id"
CONF_MIN_SOC = "min_soc"
CONF_MAX_SOC = "max_soc"
CONF_CHARGE_POWER = "charge_power"
CONF_DISCHARGE_POWER = "discharge_power"
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

# Defaults — arbitrage
DEFAULT_CHEAP_HOURS = 4
DEFAULT_EXPENSIVE_HOURS = 4
DEFAULT_MIN_PRICE_DELTA = 0.06
DEFAULT_PRICE_FORECAST_ATTR = "forecast"
DEFAULT_STRATEGY = STRATEGY_OFF

# Defaults — PID
DEFAULT_TARGET_GRID_POWER = 0
DEFAULT_PID_KP = 1.0
DEFAULT_PID_KI = 0.05
DEFAULT_PID_KD = 0.01
DEFAULT_PID_DEADBAND = 15
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

# force_mode select options
FORCE_MODE_STANDBY = "standby"
FORCE_MODE_CHARGE = "charge"
FORCE_MODE_DISCHARGE = "discharge"

# Update intervals
UPDATE_INTERVAL = 60           # seconds — main coordinator
PID_UPDATE_INTERVAL = 15       # seconds — PID tick (self_consumption, charge_pv)
