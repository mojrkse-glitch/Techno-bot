import re
import logging
import time
from decimal import Decimal, ROUND_UP
from threading import Lock

import requests

import database as db
import commerce_db
import suppliers

from app_settings import BRAND_ICONS, JRK_API_KEY, JRK_API_URL, PROFIT_MARGIN

CACHE = {}
CATALOG_TTL = 300
FAIL_BACKOFF = 15
RETRY_AFTER = {}
CATALOG_LOCK = {"ar": Lock(), "en": Lock()}
CAT_RULES = {
    "games": ("xbox", "game", "steam", "psn", "pubg", "roblox", "valorant", "playstation", "جيم", "ألعاب", "ببجي"),
    "ai": ("gpt", "gemini", "claude", "midjourney", "perplexity", "grok", "جيميناي", "ذكاء"),
    "design": ("capcut", "canva", "adobe", "premiere", "photoshop", "كاب كات", "كانفا", "أدوبي"),
    "streaming": ("youtube", "netflix", "spotify", "disney", "shahid", "iptv", "يوتيوب", "نتفليكس", "شاهد"),
}


def pricing_snapshot():
    markup, overrides = db.pricing_settings()
    return ((Decimal(str(PROFIT_MARGIN)) - 1) * 100 if markup is None else markup), overrides


def calc_price(cost, product_id=None, quantity=1, pricing=None):
    cost = Decimal(str(cost))
    markup, overrides = pricing if pricing is not None else pricing_snapshot()
    mode, value = overrides.get(product_id, ("percent", markup))
    flash=commerce_db.edits().get(product_id,{})
    if flash.get("flash_until",0)>time.time() and flash.get("flash_price"):
        special=Decimal(flash["flash_price"])*quantity
        if special>=cost: return special
    if mode == "fixed":
        price = value * quantity
        return price if price >= cost else None
    return (cost * (1 + value / 100)).quantize(Decimal("0.01"), rounding=ROUND_UP)


def jrk_request(method, endpoint, payload=None, extra_headers=None, lang="en", timeout=30):
    headers = {
        "Authorization": f"Bearer {JRK_API_KEY}",
        "Accept": "application/json",
        "Accept-Language": "ar" if lang == "ar" else "en",
    }
    if extra_headers:
        headers.update(extra_headers)
    try:
        response = requests.request(method, f"{JRK_API_URL}{endpoint}", json=payload, headers=headers, timeout=timeout)
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        logging.getLogger(__name__).warning("JRK request failed (%s)",type(exc).__name__)
        return 0, {}
    return (response.status_code, data) if isinstance(data, dict) else (0, {})


def fetch_reseller_wallet():
    status, data = jrk_request("GET", "/me")
    return data.get("wallet", {}) if status == 200 else {}


def get_icon(name):
    aliases = {"كاب كات": "capcut", "يوتيوب": "youtube", "جيميناي": "gemini",
               "نتفليكس": "netflix", "كانفا": "canva", "إكس بوكس": "xbox"}
    name = name.casefold()
    for alias, brand in aliases.items():
        name = name.replace(alias, brand)
    return next((icon for brand, icon in BRAND_ICONS.items()
                 if brand != "default" and brand in name), BRAND_ICONS["default"])


def detect_category(name):
    name = name.casefold()
    for category, keywords in CAT_RULES.items():
        if any(keyword in name for keyword in keywords):
            return category
    if re.search(r"\bai\b", name):
        return "ai"
    return "other"


def load_catalog(lang, force):
    lang = "ar" if lang == "ar" else "en"
    with CATALOG_LOCK[lang]:
        timestamp, cached = CACHE.get(lang, (0, []))
        now = time.monotonic()
        if not force and (now < RETRY_AFTER.get(lang, 0) or
                          (lang in CACHE and now - timestamp < CATALOG_TTL)):
            return cached
        return fetch_catalog(lang, cached)


def fetch_catalog(lang, cached):
    items = []
    seen_offsets = set()
    offset = 0
    while offset not in seen_offsets:
        seen_offsets.add(offset)
        status, data = jrk_request("GET", f"/products?offset={offset}&limit=100", lang=lang, timeout=(2, 5))
        if status != 200 or not isinstance(data.get("data"), list):
            RETRY_AFTER[lang] = time.monotonic() + FAIL_BACKOFF
            return cached
        items.extend(dict(product, smart_cat=detect_category(product["name"]), icon=get_icon(product["name"]))
                     for product in data["data"])
        offset = data.get("pagination", {}).get("next_offset")
        if offset is None:
            RETRY_AFTER.pop(lang, None)
            CACHE[lang] = (time.monotonic(), items)
            return items
    RETRY_AFTER[lang] = time.monotonic() + FAIL_BACKOFF
    return cached


def get_products(lang="en", force=False, include_unavailable=False):
    """كتالوج JRK مدموجاً مع المورد الثاني (الأرخص المتاح للمنتجات المرتبطة)."""
    products = suppliers.merge(load_catalog(lang, force), force=force)
    for product in products:
        if "icon" not in product:
            product["icon"] = get_icon(product["name"])
            product["smart_cat"] = detect_category(product["name"])
    overrides = commerce_db.edits()
    result = []
    for product in products:
        meta = overrides.get(product["id"], {})
        if not include_unavailable and (meta.get("hidden") or product.get("availability") == "unavailable"):
            continue
        result.append(dict(product, shop_meta=meta, description=meta.get("description") or product.get("description"),
                           smart_cat=meta.get("category") or product["smart_cat"]))
    return sorted(result, key=lambda item: item["shop_meta"].get("position", 10000))



def listing_products(lang="en", force=False):
    """منتجات التصفح: تتضمن غير المتوفر (بزر أحمر) إذا كان الإعداد مفعّلاً. الشراء يبقى محصوراً بالمتوفر."""
    return [product for product in get_products(lang, force, include_unavailable=suppliers.show_unavailable())
            if not product["shop_meta"].get("hidden")]


def get_categories(lang="en"):
    return sorted({product["smart_cat"] for product in listing_products(lang)})
