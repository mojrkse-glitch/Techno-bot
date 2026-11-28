"""Runtime settings. Environment values override a private legacy config module."""
import json
import os

try:
    import config as legacy
except ModuleNotFoundError as exc:
    if exc.name != "config":
        raise
    legacy = None


def setting(name, default="", parse=str):
    value = os.environ.get(name)
    return parse(value) if value is not None else getattr(legacy, name, default)


BOT_TOKEN = setting("BOT_TOKEN")
ADMIN_ID = setting("ADMIN_ID", 0, int)
FORCE_SUB_CHANNEL = setting("FORCE_SUB_CHANNEL", "@اسم_قناتك")
FORCE_SUB_LINK = setting("FORCE_SUB_LINK")
SUPPORT_USERNAME = setting("SUPPORT_USERNAME")
JRK_API_KEY = setting("JRK_API_KEY")
JRK_API_URL = setting("JRK_API_URL")
LOW_BALANCE_THRESHOLD = setting("LOW_BALANCE_THRESHOLD", 10, float)
BRAND_ICONS = setting("BRAND_ICONS", {"default": "📦"}, json.loads)
PROFIT_MARGIN = setting("PROFIT_MARGIN", 1.2, float)
CV_WEBAPP_URL = setting("CV_WEBAPP_URL")
PARTNER_API_KEY = setting("PARTNER_API_KEY")
PARTNER_API_URL = setting("PARTNER_API_URL")
