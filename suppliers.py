"""دمج مورد ثانٍ (Partner API) مع كتالوج JRK.

- المنتجات المرتبطة (نفس المنتج عند المورّدَين) تظهر مرة واحدة، والشراء يتم من المورد الأرخص المتاح.
- معرّفات منتجات المورد الثاني تبدأ بـ gs_ حتى تبقى آمنة في callback_data وروابط /start.
- الربط محفوظ في الجدولين الجديدين supplier_links و supplier_rejected (إضافة فقط، لا تغيير على الجداول القائمة).
"""
import html as html_lib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from threading import Lock

import catalog
import commerce_db as store
import partner_api
from database import transaction
from platform_db import rows

LOG = logging.getLogger(__name__)
PREFIX = "gs_"
SHORT = "GS"
SUPPORTED = {"LINK", "COUPON", "READY_ACCOUNT"}
CATALOG_TTL = 120
FAIL_BACKOFF = 30
TARGET_INPUTS = {"email", "activation_identifier"}
STOCK_KEYS = ("stock_count", "stock", "available_quantity", "quantity_available", "available_stock", "inventory")

_LOCK = Lock()
_CACHE = {"at": None, "items": [], "fail_until": 0.0, "version": 0}
_AUTO = {}
_AUTO_KEY = [None, {}]
_DESC = {}
DESC_TTL = 600


def money(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


class Quote(Decimal):
    """تكلفة مقتبسة تحمل معها المورد المختار ومرجعه (معرّف JRK أو slug المورد الثاني)."""
    supplier = "jrk"
    ref = None


def make_quote(value, supplier, ref):
    quote = Quote(value)
    quote.supplier = supplier
    quote.ref = ref
    return quote


# ───────────────────────── الإعدادات ─────────────────────────

def partner_enabled(settings=None):
    settings = store.settings() if settings is None else settings
    return partner_api.configured() and settings.get("gs_enabled", "1") != "0"


def show_unavailable(settings=None):
    settings = store.settings() if settings is None else settings
    return settings.get("show_unavailable", "1") != "0"


def max_quantity(settings=None):
    settings = store.settings() if settings is None else settings
    try:
        return max(1, min(50, int(settings.get("gs_max_qty", "1"))))
    except ValueError:
        return 1


def auto_link_enabled(settings=None):
    settings = store.settings() if settings is None else settings
    return settings.get("gs_auto_link", "0") == "1"


# ───────────────────────── كتالوج المورد الثاني ─────────────────────────

def normalize_item(raw):
    """يحوّل منتجاً من Partner API إلى نفس شكل منتج JRK الذي يفهمه بقية البوت."""
    if not isinstance(raw, dict):
        return None
    try:
        number = int(raw["id"])
    except (KeyError, TypeError, ValueError):
        return None
    slug = str(raw.get("slug") or "")
    cost = money(raw.get("yourPrice"))
    if not slug or cost is None or raw.get("deliveryType") not in SUPPORTED:
        return None
    stock = raw.get("stock") if isinstance(raw.get("stock"), dict) else {}
    count = stock.get("count") if isinstance(stock.get("count"), int) and not isinstance(stock.get("count"), bool) else None
    in_stock = bool(stock.get("inStock")) and (count is None or count > 0)
    top = stock.get("maxQuantity") if isinstance(stock.get("maxQuantity"), int) else count
    days = raw.get("durationDays") if isinstance(raw.get("durationDays"), int) else None
    warranty = raw.get("warranty") if isinstance(raw.get("warranty"), dict) else {}
    provider = raw.get("provider") if isinstance(raw.get("provider"), dict) else {}
    item = {
        "id": PREFIX + str(number), "name": str(raw.get("name") or slug)[:200],
        "description": str(raw.get("description") or ""), "price": str(cost),
        "availability": "available" if in_stock else "unavailable", "input": {},
        "supplier": "gs", "partner_slug": slug, "partner_code": raw.get("productCode"),
        "stock_count": count if in_stock else 0, "stock_max": top if in_stock else 0,
        "duration_days": days, "delivery_type": raw["deliveryType"], "provider_name": provider.get("name") or provider.get("key") or "",
    }
    if days:
        item["duration"] = f"{days} days"
    if warranty.get("enabled") and warranty.get("days"):
        item["warranty"] = f"{warranty['days']} days"
    return item


def partner_items(force=False, settings=None):
    settings = store.settings() if settings is None else settings
    if not partner_enabled(settings):
        return []
    now = time.monotonic()
    with _LOCK:
        fresh = _CACHE["at"] is not None and now - _CACHE["at"] < CATALOG_TTL
        if not force and (fresh or now < _CACHE["fail_until"]):
            return _CACHE["items"]
    raw = partner_api.catalog()
    with _LOCK:
        if raw is None:
            _CACHE["fail_until"] = now + FAIL_BACKOFF
            return _CACHE["items"]
        _CACHE.update(at=now, items=[item for item in map(normalize_item, raw) if item], fail_until=0.0,
                      version=_CACHE["version"] + 1)
        return _CACHE["items"]


def reset_cache():
    with _LOCK:
        _CACHE.update(at=None, items=[], fail_until=0.0, version=_CACHE["version"] + 1)
        _AUTO.clear()
        _AUTO_KEY[:] = [None, {}]


def with_quantity(item, limit):
    top = min(limit, item.get("stock_max") or 0)
    result = dict(item)
    result["input"] = {"type": "quantity", "min_quantity": 1, "max_quantity": top} if top > 1 else {}
    return result


def jrk_stock(product):
    """يقرأ كمية المخزون من منتج JRK إن كان الـ API يعرضها (أسماء الحقول محتملة)."""
    for key in STOCK_KEYS:
        value = product.get(key)
        if isinstance(value, dict):
            value = next((value[k] for k in ("count", "quantity", "available") if isinstance(value.get(k), int)), None)
        if isinstance(value, str) and value.isdigit():
            value = int(value)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def needs_target(product):
    return (product.get("input") or {}).get("type") in TARGET_INPUTS


# ───────────────────────── مطابقة المنتجات ─────────────────────────

STOP = {"the", "a", "an", "for", "of", "and", "with", "plan", "subscription", "sub", "account", "key", "code",
        "link", "instant", "new", "private", "شهر", "اشتراك", "حساب", "باقه", "خطه", "من", "مع", "في"}
DURATIONS = (
    (re.compile(r"\b(\d{1,3}) ?(?:days?|d|يوم|ايام)\b"), 1),
    (re.compile(r"\b(\d{1,2}) ?(?:weeks?|w|اسبوع|اسابيع)\b"), 7),
    (re.compile(r"\b(\d{1,2}) ?(?:months?|mos?|m|شهر|اشهر|شهور)\b"), 30),
    (re.compile(r"\b(\d{1,2}) ?(?:years?|yrs?|y|سنه|سنوات)\b"), 365),
)
WORD_DAYS = {"monthly": 30, "annual": 365, "yearly": 365, "شهري": 30, "سنوي": 365}


def parse_days(normalized):
    for pattern, factor in DURATIONS:
        match = pattern.search(normalized)
        if match:
            return int(match.group(1)) * factor
    return next((days for word, days in WORD_DAYS.items() if re.search(rf"\b{word}\b", normalized)), None)


def strip_duration(normalized):
    for pattern, _ in DURATIONS:
        normalized = pattern.sub(" ", normalized)
    for word in WORD_DAYS:
        normalized = re.sub(rf"\b{word}\b", " ", normalized)
    return normalized


def bucket(days):
    """12 شهراً = 360 أو 365 يوماً تُعدّ مدة واحدة؛ ما دون شهر تُقارن بالأيام."""
    if not days:
        return None
    return ("m", round(days / 30.4)) if days >= 28 else ("d", days)


def features(name, days=None):
    normalized = catalog.normalize(name)
    tokens = {token for token in strip_duration(normalized).split() if token not in STOP}
    return {"brand": catalog.brand_for(name), "days": bucket(days or parse_days(normalized)), "tokens": tokens}


def similarity(a, b):
    """درجة من 0 إلى 1. صفر إذا اختلفت العلامة أو المدة المعروفة."""
    if not a["tokens"] or not b["tokens"]:
        return 0.0
    if a["days"] and b["days"] and a["days"] != b["days"]:
        return 0.0
    overlap = len(a["tokens"] & b["tokens"]) / len(a["tokens"] | b["tokens"])
    if a["brand"] != b["brand"]:
        return 0.0
    if a["brand"] == catalog.OTHER_BRAND and overlap < 1:
        return 0.0
    if a["days"] and b["days"]:
        return min(1.0, overlap + 0.1)
    return max(0.0, overlap - 0.15) if (a["days"] or b["days"]) else overlap * 0.8


def level(score, a, b):
    if score >= 0.85 and a["days"] and a["days"] == b["days"]:
        return "high"
    return "medium" if score >= 0.5 else "low"


def link_rows():
    return rows("SELECT primary_id, partner_id, origin FROM supplier_links")


def rejected_pairs():
    return {(r["primary_id"], r["partner_id"]) for r in rows("SELECT primary_id, partner_id FROM supplier_rejected")}


def suggestions(jrk, partner, minimum=0.5, links=None):
    """أفضل مطابقة لكل منتج من المورد الثاني غير مربوط، بدون تكرار (مطابقة واحد لواحد)."""
    links = {r["primary_id"]: r["partner_id"] for r in link_rows()} if links is None else links
    taken_partner = set(links.values())
    rejected = rejected_pairs()
    primaries = [(p, features(p["name"])) for p in jrk if p["id"] not in links and not needs_target(p)]
    candidates = []
    for q in partner:
        if q["id"] in taken_partner:
            continue
        qf = features(q["name"], q.get("duration_days"))
        for p, pf in primaries:
            if (p["id"], q["id"]) in rejected:
                continue
            score = similarity(pf, qf)
            if score >= minimum:
                candidates.append((score, p, q, level(score, pf, qf)))
    candidates.sort(key=lambda row: (-row[0], row[1]["name"]))
    used_p, used_q, result = set(), set(), []
    for score, p, q, lvl in candidates:
        if p["id"] in used_p or q["id"] in used_q:
            continue
        used_p.add(p["id"])
        used_q.add(q["id"])
        result.append({"primary": p, "partner": q, "score": round(score, 2), "level": lvl})
    return result


def candidates_for(primary, partner, limit=6):
    """أقرب منتجات المورد الثاني لمنتج JRK معيّن (للربط اليدوي)، حتى لو كانت الدرجة منخفضة."""
    taken = {r["partner_id"] for r in link_rows()}
    pf = features(primary["name"])
    scored = []
    for q in partner:
        if q["id"] in taken:
            continue
        qf = features(q["name"], q.get("duration_days"))
        shared = len(pf["tokens"] & qf["tokens"]) / max(1, len(pf["tokens"] | qf["tokens"]))
        scored.append((similarity(pf, qf) or shared * 0.4, q))
    scored.sort(key=lambda row: -row[0])
    return [q for _, q in scored[:limit]]


def effective_links(jrk, partner, settings):
    manual = {r["primary_id"]: r["partner_id"] for r in link_rows()}
    auto = {}
    if auto_link_enabled(settings) and partner:
        key = (_CACHE["version"], hash(tuple(p["id"] for p in jrk)), tuple(sorted(manual.items())))
        with _LOCK:
            cached = _AUTO_KEY[1] if _AUTO_KEY[0] == key else None
        if cached is None:
            cached = {row["primary"]["id"]: row["partner"]["id"]
                      for row in suggestions(jrk, partner, links=manual) if row["level"] == "high"}
            with _LOCK:
                _AUTO_KEY[:] = [key, cached]
        auto = cached
    with _LOCK:
        _AUTO.clear()
        _AUTO.update(auto)
    return {**auto, **manual}


def link(primary_id, partner_id, actor, origin="manual"):
    with transaction(write=True) as conn:
        conn.execute("DELETE FROM supplier_links WHERE primary_id=? OR partner_id=?", (primary_id, partner_id))
        conn.execute("INSERT INTO supplier_links(primary_id,partner_id,origin,created,actor) VALUES(?,?,?,?,?)",
                     (primary_id, partner_id, origin, int(time.time()), actor))
        conn.execute("DELETE FROM supplier_rejected WHERE primary_id=? AND partner_id=?", (primary_id, partner_id))
        store.audit(conn, "supplier_link", actor, new=f"{primary_id} + {partner_id}", detail=origin)


def unlink(partner_id, actor):
    with transaction(write=True) as conn:
        row = conn.execute("SELECT primary_id FROM supplier_links WHERE partner_id=?", (partner_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM supplier_links WHERE partner_id=?", (partner_id,))
        conn.execute("INSERT OR IGNORE INTO supplier_rejected VALUES(?,?)", (row["primary_id"], partner_id))
        store.audit(conn, "supplier_unlink", actor, old=f"{row['primary_id']} + {partner_id}")
    with _LOCK:
        _AUTO_KEY[:] = [None, {}]
        for key in [k for k, v in _AUTO.items() if v == partner_id]:
            del _AUTO[key]
    return True


def reject(primary_id, partner_id, actor):
    with transaction(write=True) as conn:
        conn.execute("INSERT OR IGNORE INTO supplier_rejected VALUES(?,?)", (primary_id, partner_id))
        store.audit(conn, "supplier_reject", actor, new=f"{primary_id} x {partner_id}")


# ───────────────────────── الدمج في الكتالوج ─────────────────────────

def offer(product):
    return {"cost": money(product.get("price")), "available": product.get("availability") != "unavailable",
            "stock": product.get("stock_count") if "stock_count" in product else jrk_stock(product)}


def merge(jrk, force=False):
    """يرجع قائمة جديدة: منتجات JRK (مع اختيار الأرخص للمرتبط منها) + منتجات المورد الثاني غير المرتبطة."""
    settings = store.settings()
    partner = partner_items(force, settings)
    limit = max_quantity(settings)
    by_id = {item["id"]: with_quantity(item, limit) for item in partner}
    links = effective_links(jrk, partner, settings)
    merged = []
    for product in jrk:
        item = dict(product, supplier="jrk", stock_count=jrk_stock(product))
        twin = by_id.get(links.get(product["id"]))
        if twin and not needs_target(product):
            offers = {"jrk": offer(item), "gs": offer(twin)}
            usable = [(key, o) for key, o in offers.items() if o["available"] and o["cost"] is not None]
            item["offers"] = offers
            item["partner_id"] = twin["id"]
            if usable:
                key, best = min(usable, key=lambda row: row[1]["cost"])
                item.update(supplier=key, price=str(best["cost"]), stock_count=best["stock"], availability="available")
                if key == "gs":
                    item["input"] = twin["input"]
                    item["partner_slug"] = twin["partner_slug"]
            else:
                item["availability"] = "unavailable"
        merged.append(item)
    jrk_ids = {p["id"] for p in jrk}
    linked = {v for k, v in links.items() if k in jrk_ids}
    merged.extend(item for pid, item in by_id.items() if pid not in linked)
    return merged


# ───────────────────────── عروض الأسعار والشراء ─────────────────────────

def sources(pid):
    """المصادر الممكنة لشراء هذا المنتج بالترتيب: JRK أولاً ثم المورد الثاني."""
    pairs = {r["primary_id"]: r["partner_id"] for r in link_rows()}
    with _LOCK:
        pairs = {**_AUTO, **pairs}
    if pid.startswith(PREFIX):
        primary = next((k for k, v in pairs.items() if v == pid), None)
        found = [("jrk", primary), ("gs", pid)] if primary else [("gs", pid)]
    else:
        found = [("jrk", pid), ("gs", pairs[pid])] if pid in pairs else [("jrk", pid)]
    if not partner_enabled():
        found = [row for row in found if row[0] != "gs"]
    return found


def jrk_quote(pid, quantity, timeout):
    import api
    status, data = api.jrk_request("POST", "/quote", {"product_id": pid, "quantity": quantity}, timeout=timeout)
    if status == 200 and data.get("available") is True and data.get("currency", "USD") == "USD":
        if "quantity" in data and data["quantity"] != quantity:
            return None
        total = money(data.get("total"))
        return make_quote(total, "jrk", pid) if total is not None else None
    return None


def partner_quote(pid, quantity, timeout):
    """عرض سعر طازج من المورد الثاني. التكلفة = سعر الوحدة × الكمية (بدون خصم الجملة، لحماية الهامش)."""
    try:
        number = int(pid[len(PREFIX):])
    except ValueError:
        return None
    if not 1 <= quantity <= max_quantity():
        return None
    item = normalize_item(partner_api.product(number, timeout=timeout))
    if not item or item["availability"] != "available" or quantity > (item["stock_max"] or 0):
        return None
    return make_quote(Decimal(item["price"]) * quantity, "gs", item["partner_slug"])


def quote(pid, quantity, timeout=15, only=None):
    """أرخص عرض متاح الآن بين مصادر المنتج. يرجع Quote (Decimal) أو None."""
    chosen = [row for row in sources(pid) if only in (None, row[0])]
    if not chosen:
        return None

    def run(row):
        supplier, ref = row
        try:
            return jrk_quote(ref, quantity, timeout) if supplier == "jrk" else partner_quote(ref, quantity, timeout)
        except Exception:
            LOG.warning("Supplier quote failed (%s)", supplier)
            return None

    if len(chosen) == 1:
        results = [run(chosen[0])]
    else:
        with ThreadPoolExecutor(max_workers=len(chosen)) as pool:
            results = list(pool.map(run, chosen))
    valid = [item for item in results if item is not None]
    return min(valid) if valid else None


def order_body(pid, quantity, cost, state):
    """جسم الطلب المحفوظ في checkout_orders.body. غياب المفتاح supplier يعني JRK (توافق مع الطلبات القديمة)."""
    supplier = getattr(cost, "supplier", "jrk")
    ref = getattr(cost, "ref", None) or pid
    if supplier == "gs":
        return {"supplier": "gs", "product_slug": ref, "quantity": quantity, "max_total": str(cost)}
    body = {"product_id": ref, "max_total": str(cost)}
    if (state.get("input") or {}).get("type") == "quantity":
        body["quantity"] = quantity
    if state.get("target"):
        body["activation_identifier"] = state["target"]
    return body


def guard(slug, quantity, max_total, timeout=10):
    """فحص قبل الإرسال الفعلي: ok / unavailable / price / unknown."""
    data = partner_api.product(slug, timeout=timeout)
    if data is None:
        return "unknown"
    item = normalize_item(data)
    if not item or item["availability"] != "available" or quantity > (item["stock_max"] or 0):
        return "unavailable"
    if max_total is not None and Decimal(item["price"]) * quantity > Decimal(str(max_total)):
        return "price"
    return "ok"


def describe(item):
    """وصف نصي لمنتج المورد الثاني (قائمة الكتالوج لا تحمل الوصف، فنجلبه عند فتح المنتج ونخزّنه 10 دقائق)."""
    if item.get("description"):
        return item["description"]
    pid = item["id"] if item["id"].startswith(PREFIX) else item.get("partner_id")
    if item.get("supplier") != "gs" or not pid:
        return ""
    now = time.monotonic()
    with _LOCK:
        cached = _DESC.get(pid)
    if cached and now - cached[0] < DESC_TTL:
        return cached[1]
    try:
        data = partner_api.product(int(pid[len(PREFIX):]), timeout=6) or {}
    except ValueError:
        data = {}
    text = str(data.get("description") or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", text)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()[:1200]
    with _LOCK:
        _DESC[pid] = (now, text)
    return text


def supplier_name(body_json):
    try:
        body = json.loads(body_json or "{}")
    except ValueError:
        body = {}
    return "المورد الثاني" if isinstance(body, dict) and body.get("supplier") == "gs" else "JRK"


def partner_balance():
    value = partner_api.balance() if partner_enabled() else None
    return value


def alert_if_low(bot, admin_id, threshold):
    value = partner_balance()
    try:
        if value is not None and Decimal(value) < Decimal(str(threshold)):
            bot.send_message(admin_id, f"⚠️ رصيد المورد الثاني منخفض · {value} USD")
    except InvalidOperation:
        pass
