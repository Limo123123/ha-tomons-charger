"""Constants for the Tomons / Inateck Wallbox integration."""

DOMAIN = "tomons_charger"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"
CONF_DEVICE_NAME = "device_name"
CONF_MAC = "mac_address"
CONF_PRODUCT_MODEL = "product_model_code"
CONF_CHIP_MODEL = "chip_model_code"
CONF_BROKER = "broker"
CONF_PORT = "port"
CONF_MQTT_USER = "mqtt_user"
CONF_MQTT_PASSWORD = "mqtt_password"

DEFAULT_NAME = "Tomons Wallbox"
DEFAULT_SCAN_INTERVAL = 30  # seconds (status request interval to keep streaming alive)

MANUFACTURER = "Inateck / Tomons"

PLATFORMS = [
    "sensor",
    "binary_sensor",
    "number",
    "switch",
    "button",
]
