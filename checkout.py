import json
import logging
import os
import time
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from threading import Event, Lock, Thread
from urllib.parse import quote

from telebot import types

import api
import commerce_db as store
import partner_orders
import suppliers
from database import transaction

LOG = logging.getLogger(__name__)
WORK_LOCK = Lock()


def is_enabled():
    return os.getenv("TEC_PAY_STARS_ENABLED") == "1"


def money(value):
    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() and amount >= 0 else None
    except InvalidOperation:
        return None


def stars_price(pid, usd, quantity=1, cost=None, settings=None):
    settings = store.settings() if settings is None else settings
    unit = settings.get(f"stars:{pid}")
    rate = money(settings.get("net_usd_per_star"))
    if usd is None:
        return None
    if unit:
        stars = int(unit) * quantity
    elif rate:
        stars = int((usd / rate).to_integral_value(rounding=ROUND_CEILING))
    else:
        return None
    if cost is not None and rate and stars * rate < cost:
        return None
    return stars if 0 < stars <= 1000000 else None


def supplier_quote(pid, quantity, timeout=15, only=None):
    """أرخص تكلفة متاحة الآن بين مصادر المنتج (JRK والمورد الثاني). النتيجة Decimal تحمل .supplier و .ref."""
    return suppliers.quote(pid, quantity, timeout, only)


def price(pid, cost, quantity=1):
    return stars_price(pid, api.calc_price(cost, pid, quantity), quantity, cost)


def send_invoice(bot, order, ar):
    name = order["name"][:60]
    description = ("فاتورة Techo Pay · راجع الباقة والكمية قبل الدفع."
                   if ar else "Techo Pay invoice. Review the plan and quantity before paying.")
    bot.send_invoice(chat_id=order["user_id"], title=name, description=description,
                     invoice_payload=order["token"], currency="XTR", provider_token="",
                     prices=[types.LabeledPrice(name, order["stars"])], start_parameter=order["token"])


def precheck(bot, query):
    row = store.find_invoice(query.invoice_payload)
    valid = bool(is_enabled() and row and row["user_id"] == query.from_user.id and
                 query.currency == "XTR" and query.total_amount == row["stars"] and
                 row["expires"] >= int(time.time()) and row["state"] == "invoice" and
                 row["approval"] in (None, query.id))
    if valid:
        import database
        valid=not database.get_user(row["user_id"])["is_banned"]
    expected=(row["discount_base_stars"] or row["stars"]) if row else None
    if valid and row["product_id"] != "cv":
        meta = store.edits().get(row["product_id"], {})
        chosen = partner_orders.body_of(row).get("supplier") or "jrk"
        cost = None if meta.get("hidden") else supplier_quote(row["product_id"], row["quantity"], timeout=2.5, only=chosen)
        valid = cost is not None and cost <= Decimal(row["quote_usd"]) and price(
            row["product_id"], cost, row["quantity"]) == expected
    if valid and row["discount_base_stars"] and row["stars"]<row["discount_base_stars"] and row["quote_usd"]:
        settings=store.settings()
        net=money(settings.get("net_usd_per_star"))
        valid=bool(net and (settings.get("allow_loss")=="1" or row["stars"]*net>=Decimal(row["quote_usd"])))
    if valid and row["product_id"] == "cv":
        valid = (not store.edits().get("cv", {}).get("hidden") and not store.cv_open(row["user_id"])
                 and stars_price("cv", Decimal("5")) == expected)
    if valid:
        valid = store.approve_invoice(query.invoice_payload, query.from_user.id, query.total_amount,
                                      query.currency, query.id)
    bot.answer_pre_checkout_query(query.id, valid, error_message=None if valid else
                                  "السعر أو التوفر تغيّر. افتح الباقة من جديد. / Reopen the product to review the price.")


def recover_supplier(token):
    offset = 0
    # This read checks recent orders before retrying with the original idempotency key.
    for _ in range(3):
        status, data = api.jrk_request("GET", f"/orders?offset={offset}&limit=50")
        if status != 200:
            return None, False
        match = next((entry for entry in data.get("data", []) if entry.get("client_reference") == token), None)
        if match:
            return match, True
        offset = (data.get("pagination") or {}).get("next_offset")
        if offset is None:
            break
    return None, True


def supplier_result(row, supplier):
    oid = row["id"]
    state = supplier.get("status")
    supplier_id = supplier.get("id")
    if not supplier_id:
        return store.transition(oid, ("sending", "processing"), "review", reason="supplier_reference_missing")
    delivery = supplier.get("delivery") or {}
    instructions = str(delivery.get("instructions") or "")
    items = delivery.get("items") or []
    output = "\n".join([*(str(item) for item in items), *([instructions] if instructions else [])])
    total = money(supplier.get("total"))
    returned = money(supplier.get("refunded", 0))
    actual = (total - returned if total is not None and returned is not None and returned <= total else None)
    fields = {"supplier_id": supplier_id}
    if state == "completed":
        delivered_quantity = supplier.get("delivered_quantity")
        partial = (isinstance(delivered_quantity, int) and delivered_quantity < row["quantity"]) or bool(returned)
        target = "partial" if partial else "delivered"
        fields.update(actual_usd=str(actual) if actual is not None else None, delivery=output,
                      supplier_quantity=delivered_quantity if isinstance(delivered_quantity, int)
                      else (None if partial else row["quantity"]),
                      delivered=None if partial else int(time.time()), reason="partial_delivery" if partial else None)
    elif state in {"refunded", "cancelled", "failed"}:
        target = "failed"
        fields.update(actual_usd="0", reason="jrk_" + state)
    elif state in {"pending", "processing", "under_review"}:
        target = "processing"
        fields.update(due=int(time.time()) + 30, reason=state)
    else:
        target = "review"
        fields["reason"] = "supplier_status_unknown"
    return store.transition(oid, ("sending", "processing"), target, action="supplier", **fields)


def process(oid):
    with WORK_LOCK:
        row = store.order(oid)
        if not row or row["state"] not in {"paid", "sending", "processing"} or row["due"] > int(time.time()):
            return
        if row["paid"] is None or not row["body"]:
            return
        if partner_orders.is_partner(row):
            partner_orders.process(row)
            return
        if row["supplier_id"]:
            status, data = api.jrk_request("GET", f"/orders/{quote(row['supplier_id'], safe='')}")
        else:
            retry = row["state"] == "sending"
            if not store.transition(oid, ("paid", "sending"), "sending", action="submit_supplier"):
                return
            if retry:
                match, checked = recover_supplier(row["token"])
                if match:
                    supplier_result(row, match)
                    return
                if not checked:
                    store.transition(oid, ("sending",), "review", reason="supplier_reconciliation_unavailable")
                    return
            body = dict(json.loads(row["body"]), client_reference=row["token"])
            status, data = api.jrk_request("POST", "/orders", body,
                                           {"Idempotency-Key": row["token"]})
        error = data.get("error") or {}
        details = error.get("details") or {}
        supplier = data.get("order") or details.get("order")
        if supplier and isinstance(supplier, dict):
            supplier_result(row, supplier)
        elif row["supplier_id"]:
            store.transition(oid, ("processing", "sending"), "processing",
                             due=int(time.time()) + 60, reason="supplier_status_unavailable")
        elif status in {400, 402, 404, 409, 422} and error.get("code") != "idempotency_conflict" and not retry:
            store.transition(oid, ("sending",), "failed", reason="supplier_rejected", actual_usd="0")
        else:
            store.transition(oid, ("sending",), "review", reason="supplier_outcome_unknown")


def retry_order(oid, actor):
    row = store.order(oid)
    if not row or row["paid"] is None or not (row["approval"] or row["wallet_amount"]) or not row["body"]:
        return False
    if row["state"] not in {"review", "manual"} or row["reason"] == "unapproved_invoice":
        return False
    target = "processing" if row["supplier_id"] else "sending"
    return store.transition(oid, (row["state"],), target, actor, "approved_retry", due=int(time.time()))


def request_refund(oid, actor, reason):
    row = store.order(oid)
    if not row or row["paid"] is None:
        return False
    safe = (row["state"] == "failed"
            or (row["state"] == "review" and row["reason"] == "unapproved_invoice" and not row["approval"])
            or (row["product_id"] == "cv" and row["state"] in {"cv_details", "cv_work"}))
    return safe and store.transition(oid, (row["state"],), "refund_requested", actor, "request_refund", reason=reason)


def refund(bot, oid, actor):
    order = store.order(oid)
    if order and order["wallet_amount"] is not None:
        return store.refund_wallet(oid, actor)
    if not store.transition(oid, ("refund_requested",), "refunding", actor, "send_refund"):
        return False
    row = store.order(oid)
    try:
        if bot.refund_star_payment(row["user_id"], row["charge"]):
            return store.update_refund(row["charge"], row["user_id"], row["stars"], "XTR")
    except Exception:
        LOG.warning("Telegram refund outcome is not confirmed for order %s", oid)
    store.transition(oid, ("refunding",), "refund_unknown", actor, reason="telegram_refund_unconfirmed")
    return False


def worker(bot=None):
    stopped = Event()

    def loop():
        while not stopped.is_set():
            try:
                work = store.next_work()
                if work:
                    process(work["id"])
                else:
                    stopped.wait(3)
            except Exception:
                LOG.error("Checkout worker failed to process an order. Review application state.")
                stopped.wait(10)

    thread = Thread(target=loop, name="checkout-worker", daemon=True)
    thread.start()
    if bot is not None:
        import notifications
        import broadcast
        def notices():
            while not stopped.is_set():
                try:
                    notifications.push(bot)
                    notifications.maintenance(bot)
                except Exception:
                    LOG.error("Notification processing interrupted")
                stopped.wait(4)
        Thread(target=notices,name="notifications",daemon=True).start()
        broadcast.start(bot,stopped)

        def cv_jobs():
            import cv_pipeline
            while not stopped.is_set():
                try:
                    cv_pipeline.tick()
                except Exception:
                    LOG.error("CV processing interrupted")
                stopped.wait(3)

        Thread(target=cv_jobs, name="cv-processing", daemon=True).start()
    return stopped


def reconcile_refund(bot, oid):
    row = store.order(oid)
    if not row or row["state"] not in {"refunding", "refund_unknown", "refund_requested"}:
        return False
    try:
        for offset in range(0, 300, 100):
            items = bot.get_star_transactions(offset=offset, limit=100).transactions
            for item in items:
                receiver = getattr(item, "receiver", None)
                user = getattr(receiver, "user", None)
                if (item.id == row["charge"] and user and user.id == row["user_id"] and
                        abs(item.amount) == row["stars"] and not getattr(item, "nanostar_amount", 0)):
                    return store.update_refund(row["charge"], row["user_id"], row["stars"], "XTR")
            if len(items) < 100:
                break
    except Exception:
        LOG.warning("Telegram refund history could not be verified for order %s", oid)
    return False


def reconcile_payment(bot, oid):
    row = store.order(oid)
    if not row or row["state"] != "invoice" or row["charge"]:
        return False
    try:
        for offset in range(0, 300, 100):
            entries = bot.get_star_transactions(offset=offset, limit=100).transactions
            for item in entries:
                source = getattr(item, "source", None)
                user = getattr(source, "user", None)
                if (user and user.id == row["user_id"] and getattr(source, "transaction_type", None) == "invoice_payment"
                        and getattr(source, "invoice_payload", None) == row["token"] and item.amount == row["stars"]
                        and not getattr(item, "nanostar_amount", 0)):
                    return store.paid(row["token"], user.id, row["stars"], "XTR", item.id) == oid
            if len(entries) < 100:
                break
    except Exception:
        LOG.warning("Telegram payment history could not be verified for order %s", oid)
    return False
