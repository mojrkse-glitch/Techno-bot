"""عميل HTTP للمورد الثاني (Partner API v1).

- المفتاح يُقرأ من متغيّر البيئة PARTNER_API_KEY أو من config.py (PARTNER_API_KEY). لا يُحفظ في الكود ولا في store.db.
- كل الدوال ترجع (status, data) ولا ترمي استثناءات شبكة: status=0 يعني تعذّر الاتصال أو ردّ غير مفهوم.
"""
import logging
import os
from urllib.parse import quote

import requests

LOG = logging.getLogger(__name__)
DEFAULT_URL = "https://ggsoma.store/api/partner/v1"


def _setting(name, default=""):
    value = os.getenv(name)
    if value:
        return value.strip()
    try:
        import config
        return str(getattr(config, name, default) or default).strip()
    except ImportError:
        return default


def api_key():
    return _setting("PARTNER_API_KEY")


def base_url():
    return _setting("PARTNER_API_URL", DEFAULT_URL).rstrip("/")


def configured():
    return bool(api_key())


def request(method, path, payload=None, timeout=15):
    key = api_key()
    if not key:
        return 0, {}
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        response = requests.request(method, f"{base_url()}{path}", json=payload, headers=headers, timeout=timeout)
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        LOG.warning("Partner request failed (%s)", type(exc).__name__)
        return 0, {}
    if isinstance(data, (dict, list)):
        return response.status_code, data
    return 0, {}


def error_code(data):
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        return str(error.get("code") or "")
    return ""


def health(timeout=8):
    """يعمل بلا مفتاح. يرجع (متصل؟، وضع الصيانة؟)."""
    try:
        response = requests.get(f"{base_url()}/health", timeout=timeout)
        data = response.json()
    except (requests.RequestException, ValueError):
        return False, False
    if not isinstance(data, dict):
        return False, False
    return bool(data.get("ok")), data.get("error") == "maintenance"


def balance(timeout=10):
    status, data = request("GET", "/balance", timeout=timeout)
    if status == 200 and isinstance(data, dict) and data.get("ok") and data.get("balance") is not None:
        return str(data["balance"])
    return None


def catalog(timeout=20):
    status, data = request("GET", "/catalog/products", timeout=timeout)
    if status == 200 and isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    return None


def product(ref, timeout=10):
    status, data = request("GET", f"/catalog/products/{quote(str(ref), safe='')}", timeout=timeout)
    if status == 200 and isinstance(data, dict) and data.get("slug"):
        return data
    return None


def create_order(body, timeout=30):
    return request("POST", "/orders", body, timeout=timeout)


def get_order(code, timeout=15):
    return request("GET", f"/orders/{quote(str(code), safe='')}", timeout=timeout)
