"""تنفيذ الطلبات المدفوعة عبر المورد الثاني.

يُستدعى من checkout.process (تحت WORK_LOCK) للطلبات التي body الخاص بها يحمل supplier=gs.
الأمان المالي:
- externalOrderId = token الطلب نفسه، فإعادة الإرسال لا تنشئ طلباً ثانياً ولا تخصم مرتين.
- قبل أي إعادة محاولة نبحث عن الطلب عند المورد بواسطة externalOrderId حتى لا نعتبر طلباً سُلّم فاشلاً.
- لا تُسجَّل محتويات التسليم (روابط/أكواد/حسابات) في أي سجل.
"""
import json
import logging
import time
from urllib.parse import quote as urlquote

import commerce_db as store
import partner_api
import suppliers
from database import transaction

LOG = logging.getLogger(__name__)
REJECTED = {"OUT_OF_STOCK", "PRODUCT_NOT_FOUND", "PRODUCT_NOT_ALLOWED", "PRODUCT_UNAVAILABLE",
            "UNSUPPORTED_DELIVERY_TYPE", "INVALID_QUANTITY", "VALIDATION_ERROR"}
TRANSIENT_CODES = {"RATE_LIMIT_EXCEEDED", "MAINTENANCE"}
TRANSIENT_STATUS = {0, 429, 500, 502, 503, 504}
STATUS = {"COMPLETED": "completed", "FAILED": "failed", "CANCELLED": "cancelled", "CANCELED": "cancelled",
          "REFUNDED": "refunded", "PENDING": "pending", "PROCESSING": "processing"}
MAX_RETRIES = 10
RETRY_DELAY = 60


def body_of(row):
    try:
        body = json.loads(row["body"] or "{}")
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def is_partner(row):
    return body_of(row).get("supplier") == "gs"


def delivery_values(source):
    if not isinstance(source, dict):
        return []
    for key in ("link", "code", "content"):
        value = source.get(key)
        if value:
            return [str(value)]
    return []


def to_supplier(order):
    """يحوّل ردّ Partner API إلى الشكل الذي يفهمه checkout.supplier_result."""
    status = STATUS.get(str(order.get("status", "")).upper(), "unknown")
    delivery = order.get("delivery") if isinstance(order.get("delivery"), dict) else {}
    items = delivery_values(delivery)
    for line in order.get("lines") or []:
        if isinstance(line, dict):
            items.extend(delivery_values(line) or delivery_values(line.get("delivery")))
    if status == "completed" and not items:
        status = "unknown"
    quantity = order.get("quantity") if isinstance(order.get("quantity"), int) else None
    result = {"id": order.get("orderCode"), "status": status, "total": order.get("totalCharged"), "refunded": 0,
              "delivery": {"items": items, "instructions": str(delivery.get("instructions") or "")}}
    if order.get("lines"):
        result["delivered_quantity"] = len(items)
    elif quantity is not None:
        result["delivered_quantity"] = quantity
    return result


def retries(oid):
    with transaction() as conn:
        return conn.execute("SELECT count(*) FROM checkout_events WHERE order_id=? AND action='partner_retry'",
                            (oid,)).fetchone()[0]


def transient(row, reason):
    oid = row["id"]
    target = "processing" if row["supplier_id"] else "sending"
    if retries(oid) >= MAX_RETRIES:
        store.transition(oid, ("sending", "processing"), "review", reason="partner_unreachable")
        return
    store.transition(oid, ("sending", "processing"), target, action="partner_retry", reason=reason,
                     due=int(time.time()) + RETRY_DELAY)


def fail(row, code):
    store.transition(row["id"], ("sending", "processing"), "failed", action="supplier",
                     reason="partner_" + (code or "rejected").lower(), actual_usd="0")


def review(row, reason):
    store.transition(row["id"], ("sending", "processing"), "review", action="supplier", reason=reason)


def find_existing(token):
    """يبحث عن الطلب عند المورد بواسطة externalOrderId. يرجع (found|none|error، بيانات الطلب)."""
    status, data = partner_api.request("GET", "/orders?externalOrderId=" + urlquote(token, safe=""))
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return "error", None
    match = next((entry for entry in data["data"] if isinstance(entry, dict) and entry.get("orderCode")), None)
    if not match:
        return "none", None
    status, detail = partner_api.get_order(match["orderCode"])
    if status == 200 and isinstance(detail, dict) and detail.get("ok"):
        return "found", detail
    return "error", None


def outcome(row, status, data):
    import checkout
    if isinstance(data, dict) and data.get("ok") and data.get("orderCode"):
        checkout.supplier_result(row, to_supplier(data))
        return
    code = partner_api.error_code(data)
    if code in REJECTED:
        fail(row, code)
    elif code == "INSUFFICIENT_BALANCE":
        review(row, "partner_insufficient_balance")
    elif code in TRANSIENT_CODES or status in TRANSIENT_STATUS:
        transient(row, "partner_" + (code or "unavailable").lower())
    else:
        review(row, "partner_" + (code or "outcome_unknown").lower())


def process(row):
    oid = row["id"]
    body = body_of(row)
    if row["supplier_id"]:
        status, data = partner_api.get_order(row["supplier_id"])
        outcome(row, status, data)
        return
    retry = row["state"] == "sending"
    if not store.transition(oid, ("paid", "sending"), "sending", action="submit_supplier"):
        return
    if retry:
        state, existing = find_existing(row["token"])
        if state == "found":
            outcome(row, 200, existing)
            return
        if state == "error":
            transient(row, "partner_lookup_failed")
            return
    slug, quantity = body.get("product_slug"), body.get("quantity")
    if not slug or not isinstance(quantity, int) or quantity < 1:
        review(row, "partner_body_invalid")
        return
    verdict = suppliers.guard(slug, quantity, body.get("max_total"))
    if verdict == "unavailable":
        fail(row, "out_of_stock")
        return
    if verdict == "price":
        fail(row, "price_changed")
        return
    if verdict == "unknown":
        transient(row, "partner_product_lookup_failed")
        return
    status, data = partner_api.create_order(
        {"productSlug": slug, "quantity": quantity, "externalOrderId": row["token"]})
    outcome(row, status, data)
