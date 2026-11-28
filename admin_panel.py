import sys
from secrets import token_hex
import re
from decimal import Decimal, InvalidOperation
from html import escape

from telebot import apihelper, types

import api
import catalog
import checkout
import commerce_db as store
import cv_delivery
import cv_intake
import cv_pipeline
import requests
from io import BytesIO
import ops_console
import suppliers
import suppliers_admin
import wallet
import wallet_ui
import database as db
from app_settings import ADMIN_ID, LOW_BALANCE_THRESHOLD
from locales import t

PAGE_SIZE = 8
STATUSES = {
    "awaiting_details": "بانتظار المعلومات",
    "in_progress": "قيد التنفيذ",
    "submitting": "قيد الإرسال",
    "pending": "بانتظار المعالجة",
    "processing": "قيد المعالجة",
    "under_review": "قيد المراجعة",
    "completed": "مكتمل",
    "failed": "فشل",
    "refunded": "مسترد",
    "cancelled": "ملغى",
}


def allowed(user, chat):
    return bool(user and user.id == ADMIN_ID and chat and chat.type == "private")


def button(label, action):
    return types.InlineKeyboardButton(label, callback_data=f"adm|{action}")


def menu(*buttons):
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(*buttons)
    return keyboard


def screen(bot, chat_id, message_id, text, keyboard):
    if message_id is None:
        bot.send_message(chat_id, text, reply_markup=keyboard)
    else:
        try:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard)
        except apihelper.ApiTelegramException as exc:
            if "message is not modified" not in exc.description:
                raise


def counts_text(counts):
    return " · ".join(f"{escape(STATUSES.get(status, status))} {count}"
                      for status, count in sorted(counts.items())) or "—"


def home(bot, chat_id, message_id=None):
    users, counts = db.dashboard_stats()
    markup, _ = api.pricing_snapshot()
    counts_new = ops_console.overview()
    text = (f"🛡 <b>لوحة تحكم Techo Pay</b>\n\n"
            f"👥 المستخدمون: {users} · طلبات سابقة: {sum(counts.values())}\n"
            f"📦 طلبات جديدة مسجّلة: {len(store.metrics())}\n"
            f"💵 الزيادة العامة على التكلفة: {markup:.2f}%")
    keyboard = menu(button("📦 كل الطلبات", "ops|all|0|0"),
                    button(f"⏳ مدفوعة قيد العمل · {counts_new['active']}", "ops|active|0|0"),
                    button(f"🛠 بحاجة لمراجعة · {counts_new['review']}", "ops|review|0|0"),
                    button(f"↩️ استردادات معلّقة · {counts_new['refund']}", "ops|refund|0|0"),
                    button(f"📄 طلبات CV مفتوحة · {counts_new['cv']}", "ops|cvopen|0|0"),
                    button(f"💳 طلبات الشحن · {wallet.pending_count()}", "w_queue|0|1"),
                    button("💬 تذاكر الدعم", "tickets|0"), button("📊 الإحصائيات والمالية", "finance|7"),
                    button("💰 التسعير والكتالوج", "prices"), button("💱 سعر صرف الليرة مقابل الدولار", "w_rate"),
                    button("⚙️ إعدادات Stars", "payment_settings"),
                    button("👥 المستخدمون", "users|0"), button("🏦 حالة JRK ورصيده", "jrk"),
                    button("🔌 الموردون ومقارنة الأسعار", "sp|home"),
                    button("📒 سجل الإدارة", "audit"), button("كل طلبات CV عبر النجوم", "ops|cv|0|0"),
                    button("طلبات CV السابقة", "cv|open|0"),
                    types.InlineKeyboardButton("🏠 واجهة العميل", callback_data="main"))
    keyboard.add(types.InlineKeyboardButton("🛠 إدارة المتجر 2.0",callback_data="op|home"))
    screen(bot, chat_id, message_id, text, keyboard)


def jrk_wallet(bot, chat_id, message_id):
    wallet = api.fetch_reseller_wallet()
    balance = Decimal(wallet["balance"]) if "balance" in wallet else None
    _, usage = api.jrk_request("GET", "/usage") if balance is not None else (0, {})
    from time import time

    text = f"🏦 <b>محفظة JRK AI</b>\nوقت الاستعلام: {ops_console.date(time())}\n\n"
    if balance is None:
        text += "الرصيد غير متاح حالياً."
    else:
        held = escape(str(wallet.get("held", "—")))
        spend = escape(str(usage.get("last_30_days", {}).get("spend", "—")))
        text += f"الرصيد المتاح: <b>${balance:.2f}</b>\nالمحجوز: <b>{held}</b>\nإنفاق آخر 30 يوم: <b>{spend}</b>"
        if balance < LOW_BALANCE_THRESHOLD:
            text += "\n⚠️ <b>التحذير من انخفاض الرصيد</b>"
    screen(bot, chat_id, message_id, text, menu(button("🔄 تحديث", "jrk"), button("⬅️ رجوع", "home")))


def prices(bot, chat_id, message_id):
    markup, overrides = api.pricing_snapshot()
    text = (f"💰 <b>التسعير</b>\n\nالزيادة العامة على تكلفة المورد: <b>{markup:.2f}%</b>\n"
            f"الخدمات بتسعير خاص: <b>{len(overrides)}</b>\n\n"
            "مثال: زيادة 20% تجعل تكلفة 10$ تُباع بـ12$. تسعير الخدمة الخاص يتقدّم على الإعداد العام.")
    keyboard = menu(button("✏️ تعديل النسبة العامة", "ask|global"),
                    button("📦 تسعير الخدمات", "products|0"), button("💱 سعر صرف الليرة", "w_rate"),
                    button("⬅️ رجوع", "home"))
    screen(bot, chat_id, message_id, text, keyboard)


def add_pages(keyboard, page, more, route):
    buttons = []
    if page:
        buttons.append(button("⬅️ السابق", f"{route}|{page - 1}"))
    if more:
        buttons.append(button("التالي ➡️", f"{route}|{page + 1}"))
    if buttons:
        keyboard.row(*buttons)


def product_list(bot, chat_id, message_id, page, query):
    products = api.get_products("ar", include_unavailable=True)
    filtered = [p for p in products if query.casefold() in p["name"].casefold() or query == p["id"]]
    filtered.sort(key=lambda p: (p["name"].casefold(), p["id"]))
    pricing = api.pricing_snapshot()
    keyboard = menu(button("🔎 بحث بالاسم أو المعرّف", "ask|product_search"))
    if query:
        keyboard.add(button("✖️ مسح البحث", "product_clear"))
    start = page * PAGE_SIZE
    for product in filtered[start:start + PAGE_SIZE]:
        cost = Decimal(str(product["price"]))
        price = api.calc_price(cost, product["id"], pricing=pricing)
        label = f"ربح ${price - cost:.2f}" if price is not None else "البيع متوقف"
        keyboard.add(button(f"{product['icon']} {product['name'][:31]} · {label}", f"product|{product['id']}"))
    add_pages(keyboard, page, len(filtered) > start + PAGE_SIZE, "products")
    keyboard.add(button("⬅️ التسعير", "prices"))
    text = "📦 <b>تكاليف الخدمات والتسعير</b>\nالربح بجانب كل خدمة تقديري للوحدة. افتح الخدمة لعرض التكلفة والسعر."
    if query:
        text += f"\nالبحث: <b>{escape(query)}</b>"
    if not filtered:
        text += "\nلا توجد خدمات مطابقة حالياً."
    screen(bot, chat_id, message_id, text, keyboard)


def product_card(bot, chat_id, message_id, product_id, live=False):
    product = next((p for p in api.get_products("ar", include_unavailable=True) if p["id"] == product_id), None)
    if not product:
        screen(bot, chat_id, message_id, "الخدمة غير متاحة حالياً.", menu(button("⬅️ الخدمات", "products|0")))
        return
    pricing = api.pricing_snapshot()
    global_markup, overrides = pricing
    mode, amount = overrides.get(product_id, ("default", global_markup))
    current = {"default": f"النسبة العامة {amount:.2f}%", "percent": f"نسبة خاصة {amount:.2f}%",
               "fixed": f"سعر ثابت ${amount:.2f}"}[mode]
    cost = Decimal(str(product["price"]))
    quantity = 1
    source = "كتالوج المورد · تقدير للوحدة"
    note = "المورد يعرض الخدمة كغير متوفرة حالياً." if product.get("availability") == "unavailable" else ""
    if live:
        live_quantity = catalog.order_quantity(product)
        live_cost, quote_note = supplier_quote(product_id, live_quantity)
        if live_cost is None:
            note = quote_note
        else:
            quantity = live_quantity
            cost = live_cost
            source = f"عرض مباشر من المورد · الكمية {quantity}"
            note = ""
    price = api.calc_price(cost, product_id, quantity, pricing=pricing)
    text = (f"📦 <b>{escape(product['name'][:100])}</b>\n{source}\n\n"
            f"المورد الحالي: <b>{'المورد الثاني' if product.get('supplier') == 'gs' else 'JRK'}</b>\n"
            f"تكلفة المورد: <b>${cost:.2f}</b>\n")
    if price is None:
        configured = amount * quantity
        text += (f"سعر البيع المحدد: <b>${configured:.2f}</b>\n"
                 f"⚠️ البيع متوقف. التكلفة أعلى من السعر بـ <b>${cost - configured:.2f}</b>.")
    else:
        profit = price - cost
        percent = f"{100 * profit / price:.2f}%" if price else "—"
        text += (f"سعر البيع: <b>${price:.2f}</b>\n"
                 f"الربح الإجمالي المتوقع: <b>${profit:.2f}</b>\n"
                 f"هامش الربح من سعر البيع: <b>{percent}</b>\n"
                 "الربح المعروض هو الفرق بين سعر البيع وتكلفة JRK قبل أي مصاريف أخرى.")
    if price is not None:
        equivalent = wallet.syp_label(price, wallet.rate_for("SYP"), "ar")
        if equivalent:
            text += f"\nسعر البيع بالليرة: <b>{equivalent}</b>"
    text += f"\n\nالتسعير: {current}"
    stars = checkout.stars_price(product_id, price, quantity, cost)
    settings = store.settings()
    rate = checkout.money(settings.get("net_usd_per_star"))
    if stars:
        text += f"\nسعر الفاتورة: {stars} ⭐"
        if rate:
            gross = stars * rate - cost
            text += f"\nفرق التحويل التقديري بعد تكلفة JRK: {gross:.2f} USD"
        else:
            text += "\nعائد Stars بالدولار غير معتمد."
    else:
        text += "\nلا يوجد سعر Stars قابل للشراء لهذه الكمية."
    if note:
        text += f"\n{note}"
    keyboard = menu(button("⚡ جلب عرض سعر مباشر من المورد", f"quote|{product_id}"),
                    button("⭐ تعيين سعر النجوم", f"opask|stars|{product_id}"),
                    button("✏️ إدارة الكتالوج", f"opcatalog|{product_id}"),
                    button("💵 تعيين سعر ثابت بالدولار", f"ask|fixed|{product_id}"),
                    button("📈 تعيين نسبة زيادة خاصة", f"ask|percent|{product_id}"),
                    button("↩️ تطبيق النسبة العامة", f"reset|{product_id}"),
                    button("⬅️ الخدمات", "products|0"))
    screen(bot, chat_id, message_id, text, keyboard)


def supplier_quote(product_id, quantity):
    if suppliers.sources(product_id) != [("jrk", product_id)]:
        best = checkout.supplier_quote(product_id, quantity)
        if best is None:
            return None, "لا يوجد عرض مباشر متاح من أي مورد الآن. الأرقام المعروضة تقديرية من الكتالوج."
        return Decimal(str(best)), ""
    status, quote = api.jrk_request("POST", "/quote", {"product_id": product_id, "quantity": quantity}, lang="ar")
    if status == 200:
        if quote.get("available") is False:
            return None, "الخدمة غير متوفرة للشراء الآن حسب JRK. الأرقام المعروضة تقديرية من الكتالوج."
        if quote.get("available") and quote.get("currency", "USD") == "USD":
            try:
                total = Decimal(str(quote.get("total")))
                if total.is_finite() and total >= 0:
                    return total, ""
            except InvalidOperation:
                pass
    return None, "تعذّر جلب عرض مباشر من JRK. الأرقام المعروضة تقديرية من الكتالوج."


def users(bot, chat_id, message_id, page, query):
    rows = db.find_users(query.lstrip("@"), page)
    keyboard = menu(button("🔎 بحث بمعرّف تيليغرام أو @username", "ask|user_search"))
    if query:
        keyboard.add(button("✖️ مسح البحث", "user_clear"))
    for user in rows[:PAGE_SIZE]:
        username = f"@{user['username']}" if user["username"] else ""
        keyboard.add(button(f"{user['user_id']} {username}", f"user|{user['user_id']}"))
    add_pages(keyboard, page, len(rows) > PAGE_SIZE, "users")
    keyboard.add(button("⬅️ اللوحة", "home"))
    text = "👥 <b>المستخدمون</b>"
    if query:
        text += f"\nالبحث: <b>{escape(query)}</b>"
    if not rows:
        text += "\nلا توجد نتائج."
    screen(bot, chat_id, message_id, text, keyboard)


def user_card(bot, chat_id, message_id, user_id):
    result = db.admin_user_stats(user_id)
    keyboard = menu(button("⬅️ المستخدمون", "users|0"))
    if result is None:
        screen(bot, chat_id, message_id, "المستخدم غير موجود.", keyboard)
        return
    for key,title in (("adjust","💳 تعديل الرصيد"),("ban","🚫 حظر / فك الحظر"),("dm","💬 مراسلة")):
        keyboard.add(types.InlineKeyboardButton(title,callback_data=f"op|{key}|{user_id}"))
    user, counts, referrals, latest = result
    username = escape(f"@{user['username']}") if user["username"] else "—"
    text = (f"👤 <b>إحصائيات المستخدم</b>\n\n🆔 <code>{user_id}</code> · {username}\n"
            f"🌐 اللغة: {escape(user['lang'])}\n"
            f"💳 رصيد المحفظة: <b>${Decimal(user['balance']):.2f}</b>\n"
            f"💵 الإنفاق المسجّل: <b>${Decimal(user['total_spent']):.2f}</b>\n"
            f"🔗 الإحالات: <b>{referrals}</b>\n"
            f"📦 إجمالي الطلبات: <b>{sum(counts.values())}</b>\n{counts_text(counts)}")
    if latest:
        text += "\n\n<b>آخر الطلبات</b>"
        for row in latest:
            status = escape(STATUSES.get(row["status"], row["status"]))
            text += f"\n#{row['id']} · {escape(row['product_name'][:55])} · {status} · {escape(str(row['created_at'])[:10])}"
    new = store.metrics(uid=user_id)
    collected = sum(row["stars"] for row in new if row["charge"] and not row["refunded"])
    text += f"\n\nطلبات Stars: {len(new)} · نجوم مسجلة بعد الاسترداد: {collected} ⭐"
    for row in new[:5]:
        keyboard.add(button(f"#{row['id']} · {row['name'][:35]}", f"opview|{row['id']}"))
    screen(bot, chat_id, message_id, text, keyboard)


def cv_list(bot, chat_id, message_id, scope, page):
    rows = db.cv_orders(page, include_closed=scope == "all")
    keyboard = menu(button("كل الطلبات", "cv|all|0"), button("الطلبات المفتوحة", "cv|open|0"))
    for row in rows[:PAGE_SIZE]:
        keyboard.add(button(f"#{row['id']} · {STATUSES[row['status']]} · {row['user_id']}", f"cvcard|{row['id']}"))
    add_pages(keyboard, page, len(rows) > PAGE_SIZE, f"cv|{scope}")
    keyboard.add(button("⬅️ اللوحة", "home"))
    label = "كل طلبات CV" if scope == "all" else "طلبات CV المفتوحة"
    text = f"📄 <b>{label}</b>" + ("\nلا توجد طلبات." if not rows else "")
    screen(bot, chat_id, message_id, text, keyboard)


def cv_card(bot, chat_id, message_id, row, note=""):
    if row is None:
        screen(bot, chat_id, message_id, "الطلب غير موجود.", menu(button("⬅️ الطلبات", "cv|open|0")))
        return
    oid = row["id"]
    text = (f"📄 <b>طلب CV #{oid}</b>\n\n"
            f"المستخدم: <code>{row['user_id']}</code>\nالسعر: ${Decimal(row['sell_price']):.2f}\n"
            f"الحالة: <b>{STATUSES[row['status']]}</b>")
    if note:
        text += f"\n\n{note}"
    keyboard = menu(button("👤 إحصائيات المستخدم", f"user|{row['user_id']}"))
    if row["status"] in db.CV_OPEN_STATES:
        if row["status"] == "awaiting_details":
            keyboard.add(button("🛠 بدء التنفيذ", f"cvset|in_progress|{oid}"))
        keyboard.add(button("✅ تأكيد الإكمال", f"cvcfm|completed|{oid}"),
                     button("↩️ إلغاء وردّ الرصيد", f"cvcfm|cancelled|{oid}"))
    keyboard.add(button("⬅️ الطلبات", "cv|open|0"))
    screen(bot, chat_id, message_id, text, keyboard)


def new_cv_notice(bot, row):
    keyboard = menu(button("فتح الطلب", f"cvcard|{row['id']}"))
    return notify(bot, ADMIN_ID, f"📄 طلب CV جديد <b>#{row['id']}</b> من <code>{row['user_id']}</code>.",
                  reply_markup=keyboard)


def notify(bot, chat_id, text, **kwargs):
    try:
        bot.send_message(chat_id, text, **kwargs)
        return True
    except (apihelper.ApiException, OSError):
        return False


def handle_callback(bot, call, states):
    if not allowed(call.from_user, getattr(call.message, "chat", None)):
        bot.answer_callback_query(call.id)
        return
    uid, chat_id, msg_id = call.from_user.id, call.message.chat.id, call.message.message_id
    _, action, *args = call.data.split("|")
    state = states.get(uid, {})
    view = state.copy() if state.get("mode") == "admin" else {"mode": "admin"}
    view.pop("action", None)
    for key, commit in (("wallet_preview", "w_commit"), ("price_preview", "commit_price"), ("op_preview", "opcommit"),
                        ("cv_file_preview", "cvfilego"), ("cv_generated_preview", "cvsendpdfgo")):
        if action != commit:
            view.pop(key, None)
    states[uid] = view
    bot.answer_callback_query(call.id)
    if action.startswith("w_"):
        wallet_ui.admin_callback(sys.modules[__name__], bot, call, view, action, args)
    elif action == "home":
        home(bot, chat_id, msg_id)
    elif action == "jrk":
        jrk_wallet(bot, chat_id, msg_id)
    elif action == "prices":
        prices(bot, chat_id, msg_id)
    elif action in {"products", "product_clear"}:
        if action == "product_clear":
            view.pop("products", None)
        product_list(bot, chat_id, msg_id, int(args[0]) if args else 0, view.get("products", ""))
    elif action in {"product", "quote"}:
        product_card(bot, chat_id, msg_id, args[0], live=action == "quote")
    elif action == "reset":
        pricing_preview(bot, chat_id, msg_id, view, "default", args[0], None)
    elif action == "commit_price":
        draft = view.pop("price_preview", None)
        if not draft or draft[0] != args[0]:
            return
        _, mode, product_id, amount = draft
        if mode == "global":
            db.set_markup(amount, actor=uid)
            prices(bot, chat_id, msg_id)
        else:
            db.set_product_pricing(product_id, mode, amount, actor=uid)
            product_card(bot, chat_id, msg_id, product_id)
    elif action in {"users", "user_clear"}:
        if action == "user_clear":
            view.pop("users", None)
        users(bot, chat_id, msg_id, int(args[0]) if args else 0, view.get("users", ""))
    elif action == "user":
        user_card(bot, chat_id, msg_id, int(args[0]))
    elif action == "cv":
        cv_list(bot, chat_id, msg_id, args[0], int(args[1]))
    elif action == "cvcard":
        cv_card(bot, chat_id, msg_id, db.cv_order(int(args[0])))
    elif action == "cvcfm":
        target, oid = args
        row = db.cv_order(int(oid))
        if row is None or row["status"] not in db.CV_OPEN_STATES:
            cv_card(bot, chat_id, msg_id, row)
            return
        label = "إكمال الطلب" if target == "completed" else "إلغاء الطلب وإرجاع 5$ لرصيد العميل"
        keyboard = menu(button("✅ تأكيد", f"cvset|{target}|{oid}"), button("⬅️ رجوع", f"cvcard|{oid}"))
        screen(bot, chat_id, msg_id, f"#{oid} · تأكيد {label}؟", keyboard)
    elif action == "cvset":
        target, oid = args
        row, changed = db.change_cv_status(int(oid), target)
        note = ""
        if changed:
            lang = db.get_user(row["user_id"])["lang"]
            sent = notify(bot, row["user_id"], t(lang, f"cv_notice_{target}", order_id=oid))
            if not sent:
                note = "تم تحديث الطلب. تعذّر إرسال إشعار تيليغرام للعميل."
        cv_card(bot, chat_id, msg_id, row, note)
    elif action in {"cvfile", "cvfilere", "cvfileok", "cvfilego"}:
        cv_delivery.callback(sys.modules[__name__], bot, call, view, action, args)
    elif action in {"cvpdf", "cvsendpdf", "cvsendpdfgo", "cvresume", "cvreconcile", "cvorganized"}:
        if len(args) != 1 or not args[0].isdigit():
            return
        oid = int(args[0])
        if action == "cvresume":
            note = "أُعيدت جدولة المعالجة." if cv_pipeline.resume(oid) else "لا توجد مرحلة قابلة للاستئناف."
        elif action == "cvreconcile":
            try:
                found = cv_pipeline.reconcile(oid)
            except (requests.RequestException, ValueError, KeyError):
                found = False
            note = "استُعيد معرّف المهمة." if found else "لم يُستعد معرّف مؤكد. تحقّق في Canva قبل أي إرسال جديد."
        elif action == "cvorganized":
            job = cv_pipeline.status(oid)
            if job and job["organized"]:
                file = BytesIO(job["organized"].encode())
                file.name = f"cv_organized_{oid}.json"
                bot.send_document(chat_id, file)
            return
        else:
            file = cv_pipeline.pdf_file(oid)
            if not file:
                return
            if action == "cvpdf":
                bot.send_document(chat_id, file)
                return
            if action == "cvsendpdf":
                view["cv_generated_preview"] = oid
                screen(bot, chat_id, msg_id, f"تأكيد إرسال PDF المراجع للعميل · #{oid}؟",
                       menu(button("تأكيد الإرسال", f"cvsendpdfgo|{oid}"),
                            button("⬅️ رجوع", f"opview|{oid}")))
                return
            if view.pop("cv_generated_preview", None) != oid:
                bot.send_message(chat_id, "افتح بطاقة الطلب وأكّد الإرسال مجدداً.")
                return
            outcome, _ = cv_delivery.send(bot, oid, uid, file, f"generated-{oid}", file.name)
            note = cv_delivery.NOTES.get(outcome, "تعذّر الإرسال. راجع حالة التسليم في البطاقة.")
        ops_console.card(sys.modules[__name__], bot, chat_id, msg_id, oid, note)
    elif action == "cvjson":
        cv_intake.send_json(bot, chat_id, int(args[0]))
    elif action == "sp":
        suppliers_admin.callback(sys.modules[__name__], bot, call, view, args)
    elif action == "ask":
        ask(bot, chat_id, msg_id, view, *args)
    else:
        ops_console.callback(sys.modules[__name__], bot, call, view, action, args)


def ask(bot, chat_id, message_id, view, action, product_id=None):
    prompts = {
        "global": ("أرسل النسبة العامة، مثل 20 أو 25.5.", "prices"),
        "percent": ("أرسل نسبة الزيادة الخاصة، مثل 30.", f"product|{product_id}"),
        "fixed": ("أرسل سعر البيع للوحدة بالدولار، مثل 12.50.", f"product|{product_id}"),
        "product_search": ("أرسل اسم الخدمة أو معرّفها.", "products|0"),
        "user_search": ("أرسل معرّف تيليغرام أو @username المسجّل.", "users|0"),
    }
    prompt, back = prompts[action]
    view.update(action=action, product_id=product_id)
    screen(bot, chat_id, message_id, prompt, menu(button("⬅️ إلغاء", back)))


def handle_text(bot, message, view):
    if not allowed(message.from_user, message.chat):
        return
    action = view["action"]
    if action == "wallet_input":
        wallet_ui.admin_text(sys.modules[__name__], bot, message, view)
        return
    if action == "op_input":
        ops_console.text_input(sys.modules[__name__], bot, message, view)
        return
    if action == "cv_file":
        cv_delivery.admin_file(sys.modules[__name__], bot, message, view)
        return
    raw = (message.text or "").strip()
    chat_id = message.chat.id
    if action in {"product_search", "user_search"}:
        if not raw or len(raw) > 80:
            bot.send_message(chat_id, "أرسل كلمة بحث بطول لا يتجاوز 80 حرفاً.")
            return
        view.pop("action", None)
        key, render = ("products", product_list) if action == "product_search" else ("users", users)
        view[key] = raw
        render(bot, chat_id, None, 0, raw)
        return
    raw = raw.replace("٫", ".").replace(",", ".").rstrip("%٪ ").strip()
    if not re.fullmatch(r"\d{1,6}(?:\.\d{1,2})?", raw):
        bot.send_message(chat_id, "أرسل رقماً موجباً أو صفراً، وبحد أقصى منزلتين عشريتين.")
        return
    amount = Decimal(raw)
    product_id = view.get("product_id")
    if action == "fixed":
        product = next((p for p in api.get_products("ar", include_unavailable=True) if p["id"] == product_id), None)
        if product is None or not amount or amount < Decimal(str(product["price"])):
            bot.send_message(chat_id, "السعر الثابت لازم يغطي تكلفة المورد الحالية. تحقق من توفر الخدمة وتكلفتها.")
            return
    view.pop("action", None)
    pricing_preview(bot, chat_id, None, view, action, product_id, amount)


def pricing_preview(bot, chat, msg, view, mode, pid, amount):
    global_markup, rules = api.pricing_snapshot()
    key = token_hex(6)
    view["price_preview"] = (key, mode, pid, amount)
    if mode == "global":
        text = f"نسبة الزيادة العامة: {global_markup:.2f}% → {amount:.2f}%"
        back = "prices"
    else:
        item = next((p for p in api.get_products("ar", include_unavailable=True) if p["id"] == pid), None)
        back = f"product|{pid}"
        if item:
            cost = Decimal(str(item["price"]))
            old = api.calc_price(cost, pid, pricing=(global_markup, rules))
            updated = dict(rules)
            if mode == "default":
                updated.pop(pid, None)
            else:
                updated[pid] = (mode, amount)
            new = api.calc_price(cost, pid, pricing=(global_markup, updated))
            previous = f"${old:.2f}" if old is not None else "موقوف"
            after = f"${new:.2f}" if new is not None else "موقوف"
            profit = f"${new - cost:.2f}" if new is not None else "—"
            text = f"تكلفة JRK ${cost:.2f} · البيع {previous} → {after} · فرق البيع المتوقع {profit}"
        else:
            text = "كلفة الكتالوج غير متوفرة حالياً."
    screen(bot, chat, msg, f"<b>معاينة التسعير</b>\n{text}",
           menu(button("✅ حفظ التسعير", f"commit_price|{key}"), button("إلغاء", back)))
