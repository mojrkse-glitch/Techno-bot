from datetime import timedelta
from decimal import Decimal
from html import escape
import re
import time

from telebot import apihelper

import api
import catalog
import checkout
import commerce_db as store
import database as db
import cv_intake
import suppliers
from shop_ui import date, label

CV_DELIVERY_STATES = ("cv_details", "cv_work")
CV_DELIVERY_TIMEOUT = 120

SCOPES = {"all": "الطلبات", "active": "مدفوعة قيد المعالجة", "review": "المراجعة", "refund": "الاستردادات",
          "cv": "السيرة الذاتية", "cvopen": "طلبات السيرة المفتوحة", "invoice": "بانتظار الدفع", "completed": "المسلّمة"}
PERIODS = (0, 1, 7, 30)
FIELDS = {
    "description": "الوصف", "duration": "المدة", "region": "البلد", "device": "الجهاز",
    "requirements": "متطلبات الحساب", "warranty": "الضمان", "eta": "وقت التسليم",
    "method": "طريقة الاستلام", "category": "نوع الخدمة", "brand": "العلامة", "position": "الترتيب",
}
TRANSITIONS = {
    "manual": "إسناد للمراجعة اليدوية",
    "retry": "الموافقة على إعادة المطابقة مع JRK",
    "cvwork": "بدء إعداد السيرة",
    "cvreceived": "تأكيد وصول الملف للزبون",
    "cvretry": "إعادة فتح التسليم بعد التحقق من عدم وصول الملف",
    "refund": "طلب استرداد النجوم",
    "sendrefund": "تنفيذ استرداد النجوم",
    "partialdone": "إغلاق مراجعة التسليم الجزئي مع حفظ سبب القرار",
}


def overview():
    rows = store.metrics()
    paid = [row for row in rows if row["paid"] is not None]
    return {
        "active": sum(row["state"] in {"paid", "sending", "processing", "review", "manual", "partial", "failed"} for row in paid),
        "review": sum(row["state"] in {"review", "manual", "partial", "failed"} for row in rows),
        "refund": sum(row["state"] in {"refund_requested", "refunding", "refund_unknown"} for row in rows),
        "cv": sum(row["state"] in (*CV_DELIVERY_STATES, "cv_sending") for row in rows),
    }


def listing(panel, bot, chat, msg, scope, days, page, query):
    rows = store.orders(query, scope, page, days=days)
    menu = panel.menu(panel.button("🔎 بحث بمعرّف أو مرجع", f"opask|search|{scope}|{days}"))
    for row in rows[:8]:
        menu.add(panel.button(f"#{row['id']} · {row['name'][:30]} · {label('ar', row['state'])}", f"opview|{row['id']}"))
    panel.add_pages(menu, page, len(rows) > 8, f"ops|{scope}|{days}")
    menu.row(*(panel.button(f"{n} يوم" if n else "الكل", f"ops|{scope}|{n}|0") for n in PERIODS))
    if query:
        menu.add(panel.button("مسح البحث", f"opclear|{scope}|{days}|0"))
    menu.add(panel.button("⬅️ اللوحة", "home"))
    text = f"<b>{SCOPES[scope]}</b>" + (f" · {days} يوم" if days else "")
    if query:
        text += f"\nالبحث: {escape(query)}"
    if not rows:
        text += "\nلا توجد نتائج."
    panel.screen(bot, chat, msg, text, menu)


def cv_delivery_needs_review(row):
    return (row["product_id"] == "cv" and row["state"] == "cv_sending"
            and (row["reason"] == "cv_delivery_unconfirmed" or row["updated"] <= time.time() - CV_DELIVERY_TIMEOUT))


def card(panel, bot, chat, msg, oid, note=""):
    row = store.order(oid)
    menu = panel.menu(panel.button("⬅️ الطلبات", "ops|all|0|0"), panel.button("🔄 تحديث البطاقة", f"opview|{oid}"))
    if not row:
        panel.screen(bot, chat, msg, "الطلب غير موجود.", menu)
        return
    if cv_intake.submission(oid):
        menu.add(panel.button("⬇️ تنزيل JSON السيرة", f"cvjson|{oid}"))
    status = row["state"]
    ticket = store.get_ticket(oid)
    wallet_paid = row["wallet_amount"] is not None
    amount = f"{row['wallet_amount']} USD من المحفظة" if wallet_paid else f"{row['stars']} ⭐"
    reference = "خصم محفظة محفوظ" if wallet_paid else row["charge"] or "—"
    text = (f"<b>#{oid} · {escape(row['name'])}</b>\nعميل: <code>{row['user_id']}</code>\n"
            f"{label('ar', status)} · {date(row['updated'])}\n"
            f"الكمية: {row['quantity']} · الفاتورة: {amount}\n"
            f"الدفع: <code>{escape(reference)}</code>\n"
            f"مرجع {suppliers.supplier_name(row['body'])}: <code>{escape(row['supplier_id'] or '—')}</code>\n"
            f"عرض المورد: {escape(row['quote_usd'] or '—')} USD · الفعلي: {escape(row['actual_usd'] or '—')} USD\n"
            f"سبب الحالة: {escape(row['reason'] or '—')}")
    if row["supplier_quantity"] is not None:
        text += f"\nالكمية المسلّمة حسب المورد: {row['supplier_quantity']}/{row['quantity']}"
    actions = []
    if row["paid"] is not None:
        if not wallet_paid:
            menu.add(panel.button("تسجيل تسوية USD موثّقة", f"opask|settle|{oid}"))
        if status == "review":
            actions.append("manual")
        if status in {"review", "manual"} and row["body"] and (row["approval"] or wallet_paid):
            actions.append("retry")
        if status == "partial":
            actions.append("partialdone")
        if status == "cv_details":
            actions.append("cvwork")
        if row["product_id"] == "cv" and status in CV_DELIVERY_STATES:
            menu.add(panel.button("📎 إرسال CV للزبون", f"cvsend|{oid}"))
        if cv_delivery_needs_review(row):
            actions.extend(("cvreceived", "cvretry"))
            text += "\nتحقّق من وصول الملف مع الزبون قبل اختيار إجراء التسليم."
        if status in {"cv_details", "cv_work", "failed"} or row["reason"] == "unapproved_invoice":
            actions.append("refund")
        if status == "refund_requested":
            actions.append("sendrefund")
    if status == "invoice":
        menu.add(panel.button("مطابقة الدفع مع سجل تيليغرام", f"oppaycheck|{oid}"))
    if status in {"refunding", "refund_unknown"}:
        menu.add(panel.button("مطابقة الاسترداد مع سجل تيليغرام", f"oprefundcheck|{oid}"))
    if status == "processing":
        menu.add(panel.button("إعادة فحص المورد عند الاستحقاق", f"oprefresh|{oid}"))
    for action in actions:
        action_text = ({"refund": "طلب إعادة الرصيد للمحفظة", "sendrefund": "إعادة الرصيد للمحفظة"}.get(action, TRANSITIONS[action])
                       if wallet_paid else TRANSITIONS[action])
        menu.add(panel.button(action_text, f"opconfirm|{action}|{oid}"))
    menu.add(panel.button("إضافة ملاحظة", f"opask|note|{oid}"), panel.button("ملف المستخدم", f"user|{row['user_id']}"))
    if ticket:
        text += f"\n\n💬 <b>تذكرة الدعم</b> · {ticket['status']}\n{escape((ticket['message'] or '')[:350])}"
        if ticket["reply"]:
            text += f"\nالرد: {escape(ticket['reply'][:250])}"
        menu.add(panel.button("✉️ رد على التذكرة", f"opask|reply|{oid}"))
    if note:
        text += f"\n\n{escape(note)}"
    history = store.events(oid, 6)
    if history:
        text += "\n\n<b>آخر الأحداث</b>"
        for event in reversed(history):
            text += f"\n{date(event['at'])} · {escape(event['action'])} · {escape(event['new'] or event['detail'] or '—')[:100]}"
    panel.screen(bot, chat, msg, text, menu)


def finance(panel, bot, chat, msg, days):
    rows = store.metrics(days)
    received = [row for row in rows if row["paid"] is not None]
    delivered = [row for row in received if row["delivered"]]
    refunds = [row for row in received if row["refunded"]]
    payable = sum(row["stars"] for row in received)
    refunded = sum(row["stars"] for row in refunds)
    wallet_sales = sum((Decimal(row["wallet_amount"]) for row in received if row["wallet_amount"] is not None), Decimal("0"))
    wallet_refunds = sum((Decimal(row["wallet_amount"]) for row in refunds if row["wallet_amount"] is not None), Decimal("0"))
    cost = sum((Decimal(row["actual_usd"]) for row in received if row["actual_usd"] is not None), Decimal("0"))
    settled = [row for row in received if row["settlement_usd"] is not None and row["actual_usd"] is not None
               and not row["refunded"] and row["state"] in {"delivered", "partial_closed"}]
    profit = sum((Decimal(row["settlement_usd"]) - Decimal(row["actual_usd"]) for row in settled), Decimal("0"))
    average = str(timedelta(seconds=round(sum(row["delivered"] - row["paid"] for row in delivered) / len(delivered)))) if delivered else "—"
    favorites = {}
    for row in delivered:
        key = row["product_id"]
        favorites[key] = (row["name"], favorites.get(key, ("", 0))[1] + 1)
    rank = sorted(favorites.values(), key=lambda item: item[1], reverse=True)[:5]
    top = "\n".join(f"• {escape(name[:80])} · {count}" for name, count in rank) or "—"
    ratio = f"{100 * len(delivered) / len(received):.1f}%" if received else "—"
    with db.transaction() as conn:
        unmatched = conn.execute("SELECT COUNT(*) FROM unmatched_payments").fetchone()[0]
        legacy = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    text = (f"<b>📊 الأداء المالي · {f'{days} يوم' if days else 'كل الوقت'}</b>\n"
            f"الطلبات: {len(rows)} · مدفوعة: {len(received)} · تسليم مكتمل: {len(delivered)}\n"
            f"استردادات مؤكدة: {len(refunds)} · نسبة التسليم من المدفوعة: {ratio}\n"
            f"متوسط الدفع إلى التسليم: {average}\n\n"
            f"النجوم المحصلة من الفواتير: {payable} ⭐\nالمستردة: {refunded} ⭐\n"
            f"فرق النجوم قبل التسوية: {payable - refunded} ⭐\nمبيعات المحفظة: {wallet_sales:.2f} USD"
            f" · معاد للمحفظة: {wallet_refunds:.2f} USD\nتكلفة JRK المعروفة: {cost:.2f} USD\n"
            f"مدفوعات بلا مطابقة: {unmatched} · طلبات النظام السابق: {legacy}\n"
            f"هامش مساهمة الطلبات ذات التسوية والتكلفة المثبتتين: {f'{profit:.2f} USD' if settled else '—'}"
            f" · عددها {len(settled)}\nالفترة مبنية على تاريخ إنشاء الطلب.\n\n<b>الأعلى تسليماً</b>\n{top}")
    menu = panel.menu(panel.button("⬅️ اللوحة", "home"))
    menu.row(*(panel.button(f"{n} يوم" if n else "الكل", f"finance|{n}") for n in PERIODS))
    panel.screen(bot, chat, msg, text, menu)


def tickets(panel, bot, chat, msg, page):
    rows = store.open_tickets(page)
    menu = panel.menu(panel.button("⬅️ اللوحة", "home"))
    for row in rows[:8]:
        menu.add(panel.button(f"#{row['order_id']} · {row['user_id']} · {row['category']}",
                              f"opview|{row['order_id']}"))
    panel.add_pages(menu, page, len(rows) > 8, "tickets")
    panel.screen(bot, chat, msg, "<b>💬 تذاكر مفتوحة</b>" + ("\nلا توجد تذاكر." if not rows else ""), menu)


def audit_log(panel, bot, chat, msg):
    lines = []
    for row in store.events(limit=15):
        change = f"{str(row['old'] or '—')[:35]} → {str(row['new'] or '—')[:35]}"
        lines.append(f"{date(row['at'])} · {row['actor'] or '—'} · {escape(row['action'])}\n"
                     f"{escape(str(row['detail'] or row['order_id'] or '—')[:75])} · {escape(change)}")
    panel.screen(bot, chat, msg, "<b>سجل الإدارة والتشغيل</b>\n\n" + ("\n\n".join(lines) or "—"),
                 panel.menu(panel.button("⬅️ اللوحة", "home")))


def catalog_card(panel, bot, chat, msg, pid):
    meta = store.edits().get(pid, {})
    hidden = bool(meta.get("hidden"))
    menu = panel.menu(panel.button("إظهار" if hidden else "إخفاء", f"ophide|{pid}|{int(not hidden)}"))
    for key, title in FIELDS.items():
        menu.add(panel.button(title, f"opask|meta|{pid}|{key}"))
    menu.add(panel.button("⬅️ رجوع", "payment_settings" if pid == "cv" else f"product|{pid}"))
    lines = [f"{FIELDS[key]}: {escape(str(value)[:120])}" for key, value in meta.items() if key in FIELDS]
    panel.screen(bot, chat, msg, f"<b>تعديلات الكتالوج · {escape(pid)}</b>\n"
                 + ("مخفي" if hidden else "ظاهر عند توفره") + "\n" + "\n".join(lines), menu)


def payment_settings(panel, bot, chat, msg):
    cfg = store.settings()
    current = cfg.get("net_usd_per_star", "—")
    cv = cfg.get("stars:cv", "—")
    from wallet import enabled as wallet_enabled

    live = "مفعّل" if checkout.is_enabled() else "معطّل من إعدادات الاستضافة"
    wallet_state = "مفعّلة" if wallet_enabled() else "معطّلة"
    panel.screen(bot, chat, msg, f"<b>⚙️ إعدادات الفواتير</b>\nStars: {live}\nالمحفظة اليدوية: {wallet_state}\n"
                 f"صافي USD تقديري لكل نجمة: {escape(current)}\nالنجوم الثابتة للسيرة: {escape(cv)}\n"
                 "أدخل قيمة مبنية على التسوية المتوقعة وحدّد سعر كل منتج يدوياً عند الحاجة.",
                 panel.menu(panel.button("تعديل قيمة النجمة", "opask|rate"),
                            panel.button("تعديل سعر السيرة", "opask|stars|cv"),
                            panel.button("تفاصيل باقة السيرة", "opcatalog|cv"), panel.button("⬅️ اللوحة", "home")))


def callback(panel, bot, call, view, action, args):
    uid, chat, msg = call.from_user.id, call.message.chat.id, call.message.message_id
    if action in {"ops", "opclear"}:
        scope, days, page = args
        if action == "opclear":
            view.pop("order_query", None)
        listing(panel, bot, chat, msg, scope, int(days), int(page), view.get("order_query", ""))
    elif action in {"opview", "oprefresh"}:
        if action == "oprefresh":
            checkout.process(int(args[0]))
        card(panel, bot, chat, msg, int(args[0]))
    elif action == "oppaycheck":
        oid = int(args[0])
        confirmed = checkout.reconcile_payment(bot, oid)
        card(panel, bot, chat, msg, oid, "تأكد الدفع." if confirmed else "لم يظهر إثبات الدفع في السجل المفحوص.")
    elif action == "oprefundcheck":
        oid = int(args[0])
        confirmed = checkout.reconcile_refund(bot, oid)
        card(panel, bot, chat, msg, oid, "تأكد الاسترداد." if confirmed else "لم يظهر إثبات الاسترداد في السجل المفحوص.")
    elif action == "finance":
        finance(panel, bot, chat, msg, int(args[0]))
    elif action == "tickets":
        tickets(panel, bot, chat, msg, int(args[0]))
    elif action == "audit":
        audit_log(panel, bot, chat, msg)
    elif action == "payment_settings":
        payment_settings(panel, bot, chat, msg)
    elif action == "opcatalog":
        catalog_card(panel, bot, chat, msg, args[0])
    elif action == "ophide":
        store.edit_product(args[0], "hidden", args[1] == "1", uid)
        catalog_card(panel, bot, chat, msg, args[0])
    elif action == "cvsend" or (action == "opconfirm" and args[0] == "cvsend"):
        oid = int(args[-1])
        row = store.order(oid)
        if not row or row["product_id"] != "cv" or row["paid"] is None or row["state"] not in CV_DELIVERY_STATES:
            card(panel, bot, chat, msg, oid, "الطلب غير متاح لإرسال السيرة.")
            return
        view.update(action="cv_upload", cv_order=oid)
        panel.screen(bot, chat, msg,
                     f"📎 <b>إرسال السيرة للطلب #{oid}</b>\nالزبون: <code>{row['user_id']}</code>\n"
                     "أرفق ملف PDF أو Word هنا. سيرسله البوت مباشرةً لصاحب هذا الطلب.",
                     panel.menu(panel.button("إلغاء", f"opview|{oid}")))
    elif action == "opconfirm":
        task, oid = args
        view.update(action="op_input", op=["reason", task, oid])
        row = store.order(int(oid))
        action_text = TRANSITIONS[task]
        if row and row["wallet_amount"] is not None:
            action_text = {"refund": "طلب إعادة الرصيد للمحفظة", "sendrefund": "إعادة الرصيد للمحفظة"}.get(task, action_text)
        panel.screen(bot, chat, msg, f"<b>{action_text} · #{oid}</b>\n"
                     "أرسل سبب القرار أو مرجعه. ستظهر مراجعة قبل التنفيذ.",
                     panel.menu(panel.button("إلغاء", f"opview|{oid}")))
    elif action == "opcommit":
        token = args[0]
        draft = view.pop("op_preview", None)
        if not draft or draft[0] != token:
            return
        _, op, value = draft
        apply(panel, bot, chat, msg, uid, op, value)
    elif action == "opask":
        view.update(action="op_input", op=args)
        key = args[0]
        if key == "meta":
            prompt = "أرسل القيمة، أو اكتب - لإزالة التعديل."
            if args[2] == "category":
                prompt += " الخيارات: games, ai, design, streaming, other."
            elif args[2] == "brand":
                prompt += f" الخيارات: {', '.join(catalog.BRANDS)}."
            elif args[2] == "position":
                prompt += " أدخل رقماً من 0 إلى 9999."
        else:
            prompt = {
                "search": "أرسل رقم الطلب أو المستخدم، أو مرجع الدفع أو JRK.",
                "note": "أرسل الملاحظة الداخلية.",
                "reply": "أرسل الرد على التذكرة.",
                "settle": "أرسل صافي التسوية الفعلي بالدولار ثم مرجعها، مثال 12.50 REF-123.",
                "stars": "أرسل عدد النجوم الصحيح للوحدة أو - لتطبيق التسعير المشتق.",
                "rate": "أرسل صافي USD التقديري لكل نجمة أو - لإلغاء التسعير المشتق.",
            }[key]
        panel.screen(bot, chat, msg, prompt, panel.menu(panel.button("إلغاء", "home")))


def deliver_cv(panel, bot, message, view):
    oid = view["cv_order"]
    chat, uid = message.chat.id, message.from_user.id
    document = getattr(message, "document", None)
    name = getattr(document, "file_name", None) or ""
    file_id = getattr(document, "file_id", None)
    if not file_id or not name.lower().endswith((".pdf", ".doc", ".docx")):
        bot.send_message(chat, "أرفق السيرة كمستند بصيغة PDF أو Word (.doc أو .docx).")
        return

    row = store.order(oid)
    if (not row or row["product_id"] != "cv" or row["state"] not in CV_DELIVERY_STATES
            or not store.transition(oid, CV_DELIVERY_STATES, "cv_sending", uid, "cv_delivery_started",
                                    reason=None)):
        view.pop("action", None)
        card(panel, bot, chat, None, oid, "الطلب غير متاح لإرسال السيرة.")
        return

    lang = db.get_user(row["user_id"])["lang"]
    caption = (f"📄 سيرتك الذاتية جاهزة · طلب #{oid}" if lang == "ar"
               else f"📄 Your CV is ready · Order #{oid}")
    try:
        sent = bot.send_document(row["user_id"], file_id, caption=caption, timeout=30)
    except apihelper.ApiTelegramException as exc:
        if 400 <= exc.error_code < 500:
            store.transition(oid, ("cv_sending",), row["state"], uid, "cv_delivery_rejected", reason=row["reason"])
            bot.send_message(chat, "تعذّر إرسال الملف للزبون. جرّب إرفاقه مرة ثانية أو اضغط إلغاء.")
            return
        sent = None
    except (apihelper.ApiException, OSError):
        sent = None

    view.pop("action", None)
    if sent is None:
        store.transition(oid, ("cv_sending",), "cv_sending", uid, "cv_delivery_unconfirmed",
                         reason="cv_delivery_unconfirmed")
        card(panel, bot, chat, None, oid, "تعذّر التأكد من وصول الملف. تحقّق مع الزبون قبل إعادة الإرسال.")
        return

    saved = store.transition(oid, ("cv_sending",), "delivered", uid, "cv_file_delivered",
                             reason=f"Telegram message #{sent.message_id}", delivered=int(time.time()))
    note = ("تم إرسال ملف السيرة للزبون وتسجيل التسليم." if saved
            else "أُرسل الملف للزبون. تغيّرت حالة الطلب أثناء الإرسال، راجع البطاقة.")
    card(panel, bot, chat, None, oid, note)


def text_input(panel, bot, message, view):
    from secrets import token_hex
    value = message.text.strip()
    op = view["op"]
    uid, chat = message.from_user.id, message.chat.id
    kind = op[0]
    if not value or len(value) > (1500 if kind == "meta" and op[2] == "description" else 500):
        bot.send_message(chat, "القيمة المطلوبة ناقصة أو أطول من الحد.")
        return
    if kind == "search":
        view.pop("action", None)
        view["order_query"] = value
        listing(panel, bot, chat, None, op[1], int(op[2]), 0, value)
        return
    if kind in {"stars", "rate"} and value != "-":
        if kind == "stars" and (not re.fullmatch(r"[0-9]{1,7}", value) or not 1 <= int(value) <= 1000000):
            bot.send_message(chat, "أدخل عدداً صحيحاً من 1 إلى 1000000.")
            return
        if kind == "rate" and (not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,8})?", value) or not checkout.money(value)):
            bot.send_message(chat, "أدخل قيمة عشرية موجبة.")
            return
    if kind == "settle":
        amount, _, reference = value.partition(" ")
        if not re.fullmatch(r"[0-9]{1,8}(\.[0-9]{1,2})?", amount) or not reference.strip():
            bot.send_message(chat, "أدخل مبلغ USD ومرجعاً للتسوية.")
            return
    if kind == "meta" and value != "-":
        field = op[2]
        if (field == "category" and value not in {*api.CAT_RULES, "other"}) or (
                field == "brand" and value not in catalog.BRANDS) or (
                field == "position" and not re.fullmatch(r"[0-9]{1,4}", value)):
            bot.send_message(chat, "القيمة غير صالحة لهذا الحقل.")
            return
    view.pop("action", None)
    token = token_hex(6)
    view["op_preview"] = (token, op, value)
    old = "—"
    if kind in {"stars", "rate"}:
        key = f"stars:{op[1]}" if kind == "stars" else "net_usd_per_star"
        old = store.settings().get(key) or "—"
    preview = f"<b>معاينة</b>\n{escape(str(old))} → {escape(value)}"
    if kind == "stars" and op[1] != "cv":
        item = next((entry for entry in api.get_products("ar", include_unavailable=True) if entry["id"] == op[1]), None)
        if item:
            cost = Decimal(str(item["price"]))
            cfg = store.settings()
            cfg[f"stars:{op[1]}"] = "" if value == "-" else value
            stars = checkout.stars_price(op[1], api.calc_price(cost, op[1]), cost=cost, settings=cfg)
            rate = checkout.money(cfg.get("net_usd_per_star"))
            preview += f"\nتكلفة الوحدة: {cost:.2f} USD · الفاتورة: {f'{stars} ⭐' if stars else 'غير متاحة'}"
            if stars and rate:
                preview += f"\nفرق البيع المتوقع: {stars * rate - cost:.2f} USD"
    panel.screen(bot, chat, None, preview,
                 panel.menu(panel.button("✅ تطبيق", f"opcommit|{token}"), panel.button("إلغاء", "home")))


def apply(panel, bot, chat, msg, uid, op, value):
    kind = op[0]
    if kind == "meta":
        _, pid, field = op
        store.edit_product(pid, field, None if value == "-" else int(value) if field == "position" else value, uid)
        catalog_card(panel, bot, chat, msg, pid)
    elif kind in {"stars", "rate"}:
        key = f"stars:{op[1]}" if kind == "stars" else "net_usd_per_star"
        store.set_setting(key, "" if value == "-" else value, uid)
        if kind == "stars" and op[1] != "cv":
            panel.product_card(bot, chat, msg, op[1])
        else:
            payment_settings(panel, bot, chat, msg)
    elif kind == "settle":
        oid = int(op[1])
        raw, _, reference = value.partition(" ")
        saved = store.settle(oid, Decimal(raw), reference.strip(), uid)
        card(panel, bot, chat, msg, oid, "تم تسجيل التسوية." if saved else "لا يوجد دفع مطابق.")
    elif kind in {"note", "reply"}:
        oid = int(op[1])
        row = store.order(oid)
        if not row:
            return
        if kind == "note":
            with db.transaction(write=True) as conn:
                store.audit(conn, "admin_note", uid, oid, detail=value)
        elif store.get_ticket(oid):
            store.reply_ticket(oid, uid, value)
            panel.notify(bot, row["user_id"], f"💬 #{oid}\n{escape(value)}")
        card(panel, bot, chat, msg, oid)
    else:
        _, task, raw = op
        oid = int(raw)
        changed = False
        if task == "retry":
            changed = checkout.retry_order(oid, uid)
        elif task == "refund":
            changed = checkout.request_refund(oid, uid, value)
        elif task == "sendrefund":
            changed = checkout.refund(bot, oid, uid)
        elif task == "manual":
            changed = store.transition(oid, ("review",), "manual", uid, reason=value)
        elif task == "cvwork":
            changed = store.transition(oid, ("cv_details",), "cv_work", uid, reason=value)
        elif task in {"cvreceived", "cvretry"}:
            row = store.order(oid)
            if row and cv_delivery_needs_review(row):
                target = "delivered" if task == "cvreceived" else "cv_work"
                fields = {"delivered": int(time.time())} if target == "delivered" else {}
                changed = store.transition(oid, ("cv_sending",), target, uid, reason=value, **fields)
        elif task == "partialdone":
            changed = store.transition(oid, ("partial",), "partial_closed", uid, reason=value)
        with db.transaction(write=True) as conn:
            store.audit(conn, f"admin_{task}", uid, oid, detail=value, new="confirmed" if changed else "unchanged")
        if changed and task in {"cvwork", "refund", "sendrefund"}:
            row = store.order(oid)
            lang = db.get_user(row["user_id"])["lang"]
            panel.notify(bot, row["user_id"], f"#{oid} · {label(lang, row['state'])}")
        card(panel, bot, chat, msg, oid, "تم تنفيذ الإجراء." if changed else "لم يتأكد الإجراء. راجع الحالة المسجّلة أعلاه.")
