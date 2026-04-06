"""Constants for Just Leverage Battery."""

DOMAIN = "just_leverage_battery"

# Strategies
STRATEGY_ARBITRAGE = "arbitrage"
STRATEGY_UPS = "ups"
STRATEGY_OFF = "off"

STRATEGIES = [STRATEGY_ARBITRAGE, STRATEGY_UPS, STRATEGY_OFF]

# Config keys
CONF_MARSTEK_DEVICE_ID = "marstek_device_id"
CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_FORECAST_ATTR = "price_forecast_attr"
CONF_CHEAP_HOURS = "cheap_hours"
CONF_EXPENSIVE_HOURS = "expensive_hours"
CONF_MIN_SOC = "min_soc"
CONF_MAX_SOC = "max_soc"
CONF_CHARGE_POWER = "charge_power"
CONF_DISCHARGE_POWER = "discharge_power"
CONF_STRATEGY = "strategy"

# Defaults
DEFAULT_CHEAP_HOURS = 4
DEFAULT_EXPENSIVE_HOURS = 4
DEFAULT_MIN_SOC = 10
DEFAULT_MAX_SOC = 95
DEFAULT_CHARGE_POWER = 2000
DEFAULT_DISCHARGE_POWER = 2000
DEFAULT_PRICE_FORECAST_ATTR = "forecast"
DEFAULT_STRATEGY = STRATEGY_OFF

# Marstek services (from jaapp/ha-marstek-local-api)
MARSTEK_DOMAIN = "marstek_local_api"
MARSTEK_SERVICE_SET_PASSIVE = "set_passive_mode"
MARSTEK_SERVICE_CLEAR_SCHEDULES = "clear_manual_schedules"
MARSTEK_SERVICE_SET_SCHEDULES = "set_manual_schedules"
MARSTEK_SERVICE_AUTO_MODE = "button.marstek_auto_mode"

# Sensor names (Marstek Local API entities)
MARSTEK_SOC_SUFFIX = "battery_soc"
MARSTEK_STATE_SUFFIX = "battery_state"
MARSTEK_POWER_SUFFIX = "battery_power"

# Update interval (seconds)
UPDATE_INTERVAL = 60

# Passive mode max duration (Marstek limit = 86400s)
PASSIVE_DURATION = 3660  # 61 minutes — refreshed every hour
