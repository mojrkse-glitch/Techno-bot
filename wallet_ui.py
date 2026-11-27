from locales import ui_text
from decimal import Decimal
from html import escape
from secrets import token_hex

from telebot import types

import commerce_db as store
import database as db
import wallet

BUTTON = {"ar": "💳 المحفظة", "en": "💳 Wallet"}
STATUS = {"ar": {"pending": "قيد المراجعة", "approved": "مقبول", "rejected": "مرفوض"},
          "en": {"pending": "Under review", "approved": "Approved", "rejected": "Rejected"}}


def ui():
    import shop_ui

    return shop_ui


def translation(lang, ar, en):
    return ar if lang == "ar" else en


def name(lang):
    return BUTTON["ar" if lang == "ar" else "en"]


def number(amount):
    return format(Decimal(amount).normalize(), "f")


def currency_name(lang, currency):
    return {"SYP": "الليرة السورية (SYP)", "USD": "الدولار الأمريكي (USD)", "USDT": "USDT"}[currency] if lang == "ar" else currency


def button(text, data):
    return types.InlineKeyboardButton(text, callback_data=data)


def menu(*buttons):
    return ui().menu(*buttons)


def display(bot, chat, msg, text, keys):
    ui().render(bot, chat, msg, text, keys)


def home(bot, chat, msg, uid, lang):
    balance = db.get_user(uid)["balance"]
    keys = menu()
    if wallet.enabled():
        keys.add(button(ui_text(lang, "wallet_ui_add_funds"), "w_methods"))
    keys.add(button(ui_text(lang, "wallet_ui_topup_requests"), "w_list|0"))
    keys.add(button(ui_text(lang, "wallet_ui_main_menu"), "main"))
    text = ui_text(lang, "wallet_ui_wallet_home", balance=balance)
    if not wallet.enabled():
        text += ui_text(lang, "wallet_ui_wallet_disabled_help")
    display(bot, chat, msg, text, keys)


def customer(bot, call, lang, states):
    uid, chat, msg = call.from_user.id, call.message.chat.id, call.message.message_id
    action, *parts = call.data.split("|")
    if action == "wallet":
        states.pop(uid, None)
        home(bot, chat, msg, uid, lang)
    elif action == "w_methods":
        states.pop(uid, None)
        keys = menu()
        if wallet.enabled():
            for key, (method, *_rest) in wallet.METHODS.items():
                if store.settings().get("enable:"+key,"1")!="1": continue
                keys.add(button(method, f"w_method|{key}"))
        keys.add(button(ui_text(lang, "wallet_ui_back_wallet"), "wallet"))
        text = ui_text(lang, "wallet_ui_method_step") if wallet.enabled() else ui_text(lang, "wallet_ui_manual_disabled")
        display(bot, chat, msg, text, keys)
    elif action == "w_method" and wallet.enabled() and parts[0] in wallet.METHODS:
        method, currency, _, _, _ = wallet.METHODS[parts[0]]
        cfg=store.settings()
        if cfg.get("enable:"+parts[0],"1")!="1":
            home(bot,chat,msg,uid,lang)
            return
        rate = wallet.rate_for(currency)
        keys = menu(button(ui_text(lang, "wallet_ui_back_methods"), "w_methods"))
        if rate is None:
            text = ui_text(lang, "wallet_ui_missing_rate")
            states.pop(uid, None)
        else:
            states[uid] = {"mode": "topup", "step": "amount", "method": parts[0], "rate": rate, "nonce": token_hex(16)}
            states[uid]["destination"]=cfg.get("dest:"+parts[0],wallet.METHODS[parts[0]][2])
            picks=(5000,10000,25000,50000) if currency=="SYP" else (5,10,25,50)
            keys.row(*(button(str(n),f"quick|{states[uid]['nonce']}|{n}") for n in picks))
            text = ui_text(lang, "wallet_ui_amount_step", method=method, currency_display=currency_name(lang, currency), currency=currency)
        display(bot, chat, msg, text, keys)
    elif action == "w_list":
        states.pop(uid, None)
        page = min(int(parts[0]), 100000)
        rows = wallet.rows(uid=uid, page=page)
        keys = menu(*(button(f"#{row['id']} · {STATUS['ar' if lang == 'ar' else 'en'][row['state']]}",
                             f"w_status|{row['id']}") for row in rows[:8]))
        if page:
            keys.add(button("⬅️", f"w_list|{page - 1}"))
        if len(rows) > 8:
            keys.add(button("➡️", f"w_list|{page + 1}"))
        keys.add(button(ui_text(lang, "wallet_ui_wallet_label"), "wallet"))
        text = ui_text(lang, "wallet_ui_request_list") if rows else ui_text(lang, "wallet_ui_empty_topups")
        display(bot, chat, msg, text, keys)
    elif action == "w_status":
        states.pop(uid, None)
        row = wallet.get(int(parts[0]))
        if row and row["user_id"] == uid:
            text = summary(row, lang)
            display(bot, chat, msg, text, menu(button(ui_text(lang, "wallet_ui_back_requests"), "w_list|0")))


def summary(row, lang):
    state = STATUS["ar" if lang == "ar" else "en"][row["state"]]
    amount = number(row["amount"])
    method = wallet.METHODS[row["method"]][0]
    credit = Decimal(row["credit"])
    reference = escape(row["reference"])
    text = ui_text(lang, "wallet_ui_request_summary", id=row['id'], method=method, amount=amount, currency=row['currency'], credit=credit, reference=reference, state=state)
    if row["reason"]:
        reason = escape(row["reason"][:350])
        text += ui_text(lang, "wallet_ui_rejection_reason", reason=reason)
    return text


def collect(bot, message, lang, state, states):
    if not wallet.enabled():
        return
    uid = message.from_user.id
    method, currency, dest, ar_ref, en_ref = wallet.METHODS[state["method"]]
    dest=state.get("destination",dest)
    raw = message.text.strip()
    if state["step"] == "amount":
        cfg=store.settings()
        if cfg.get("enable:"+state["method"],"1")!="1" or dest!=cfg.get("dest:"+state["method"],wallet.METHODS[state["method"]][2]):
            states.pop(uid,None)
            bot.send_message(uid,ui_text(lang, "wallet_ui_destination_changed"),reply_markup=menu(button("💳","w_methods")))
            return
        amount = wallet.parse_amount(raw, currency)
        if amount is None or wallet.credit_for(amount, state["rate"]) <= 0:
            bot.send_message(uid, ui_text(lang, "wallet_ui_invalid_amount"))
            return
        if state["rate"] != wallet.rate_for(currency):
            states.pop(uid, None)
            bot.send_message(uid, ui_text(lang, "wallet_ui_rate_changed"),
                             reply_markup=menu(button(ui_text(lang, "wallet_ui_payment_methods"), "w_methods")))
            return
        state.update(amount=amount, step="reference")
        ref = "TxID" if currency == "USDT" else translation(lang, ar_ref, en_ref)
        value, credit = number(amount), wallet.credit_for(amount, state["rate"])
        destination = escape(dest)
        text = ui_text(lang, "wallet_ui_transfer_step", value=value, currency=currency, method=method, destination=destination, ref=ref, credit=credit)
        if currency == "SYP":
            text += ui_text(lang, "wallet_ui_request_rate")
            text += f"\n<blockquote>1 USD = {number(state['rate'])} SYP</blockquote>"
        if currency == "USDT":
            text += ui_text(lang, "wallet_ui_txid_help")
            text += ui_text(lang, "wallet_ui_net_warning")
        keys = menu(types.InlineKeyboardButton(ui_text(lang, "wallet_ui_copy_destination"),
                                               copy_text=types.CopyTextButton(dest)),
                    button(ui_text(lang, "wallet_ui_back_wallet"), "wallet"))
        bot.send_message(uid, text, reply_markup=keys)
        return
    row, outcome = wallet.submit(uid, state["method"], state["amount"], state["rate"], raw, state["nonce"],destination_snapshot=dest)
    if outcome == "invalid_reference":
        if currency == "USDT":
            hint = ui_text(lang, "wallet_ui_invalid_txid")
        elif state["method"] == "syriatel":
            hint = ui_text(lang, "wallet_ui_invalid_syriatel")
        else:
            hint = ui_text(lang, "wallet_ui_invalid_sham")
        bot.send_message(uid, f"<blockquote>⚠️ {hint}</blockquote>")
        return
    if row is None:
        bot.send_message(uid, ui_text(lang, "wallet_ui_duplicate_reference"))
        return
    if states.get(uid) is state:
        states.pop(uid, None)
    from admin_panel import ADMIN_ID, notify

    if outcome == "created":
        notify(bot, ADMIN_ID, "🔔 <b>طلب شحن جديد</b>\n" + summary(row, "ar") + f"\nالعميل: <code>{uid}</code>",
               reply_markup=menu(button("فتح الطلب", f"adm|w_card|{row['id']}")))
    bot.send_message(uid, ui_text(lang, "wallet_ui_topup_received", id=row['id']),
                     reply_markup=menu(button(ui_text(lang, "wallet_ui_view_request"), f"w_status|{row['id']}")))


def admin_ui(panel, bot, chat, msg, oid, note=""):
    row = wallet.get(oid)
    if not row:
        return
    text = summary(row, "ar") + (f"\n\nالعميل: <code>{row['user_id']}</code>\n"
           f"وجهة الاستلام: <code>{escape(row['destination'])}</code>\n"
           f"وقت الطلب: {ui().date(row['submitted'])}")
    if row["reviewed"]:
        text += f"\nوقت المراجعة: {ui().date(row['reviewed'])} · أدمن {row['reviewer']}"
    if row["verified_reference"]:
        text += f"\nمرجع التحقق: <code>{escape(row['verified_reference'])}</code>"
    if note:
        text += f"\n\n{escape(note)}"
    keys = panel.menu(panel.button("💬 مراسلة العميل", f"w_input|contact|{oid}"))
    if row["state"] == "pending":
        keys.add(panel.button("✅ قبول", f"w_input|approve|{oid}"), panel.button("❌ رفض", f"w_input|reject|{oid}"))
    keys.add(panel.button("⬅️ طلبات الشحن", "w_queue|0|1"))
    panel.screen(bot, chat, msg, text, keys)


def admin_callback(panel, bot, call, view, action, args):
    uid, chat, msg = call.from_user.id, call.message.chat.id, call.message.message_id
    if action == "w_queue":
        page, pending = int(args[0]), args[1] == "1"
        records = wallet.rows(page=page, pending=pending)
        keys = panel.menu(panel.button("قيد المراجعة", "w_queue|0|1"), panel.button("كل الطلبات", "w_queue|0|0"))
        for row in records[:8]:
            keys.add(panel.button(f"#{row['id']} · {wallet.METHODS[row['method']][0]} · {STATUS['ar'][row['state']]}",
                                  f"w_card|{row['id']}"))
        if page:
            keys.add(panel.button("⬅️", f"w_queue|{page - 1}|{int(pending)}"))
        if len(records) > 8:
            keys.add(panel.button("➡️", f"w_queue|{page + 1}|{int(pending)}"))
        keys.add(panel.button("ضبط سعر الليرة", "w_rate"), panel.button("اللوحة", "home"))
        panel.screen(bot, chat, msg, "<b>💳 طلبات الشحن اليدوي</b>" + ("\nلا توجد طلبات." if not records else ""), keys)
    elif action == "w_card":
        admin_ui(panel, bot, chat, msg, int(args[0]))
    elif action == "w_rate":
        view.update(action="wallet_input", wallet_action="rate", oid=None)
        rate = wallet.rate_for("SYP")
        current = f"1 USD = {rate:,} SYP" if rate else "غير مضبوط"
        panel.screen(bot, chat, msg, f"السعر الحالي: <b>{current}</b>\n"
                     "أدخل عدد الليرات السورية لكل دولار أمريكي. يُستخدم هذا السعر لعرض الأسعار بالليرة واحتساب رصيد طلبات الشحن الجديدة بالليرة.",
                     panel.menu(panel.button("⬅️ إلغاء", "home")))
    elif action == "w_input":
        command, oid = args[0], int(args[1])
        row = wallet.get(oid)
        if not row or (command in {"approve", "reject"} and row["state"] != "pending"):
            return
        view.update(action="wallet_input", wallet_action=command, oid=oid)
        if command == "approve":
            text = ("🔎 <b>مراجعة طلب الشحن</b>\n\n<blockquote>تحقّق من تسجيل التحويل في سجل حساب الاستلام، وراجع مرجعه وقيمته. ثم أدخل <b>صافي المبلغ المستلم فعلياً</b>.</blockquote>"
                    + ("\nأرسل المبلغ ثم معرّف المعاملة (TxID) على شبكة BEP20، مع ترك مسافة بينهما. مثال <code>10.25 0x…</code>."
                       if row["method"] == "usdt" else f"\nعملة التحويل: {row['currency']}."))
        elif command == "reject":
            text = "أرسل سبب الرفض ليظهر للعميل."
        else:
            text = "أرسل رسالتك. ستصل للعميل من البوت."
        panel.screen(bot, chat, msg, text, panel.menu(panel.button("⬅️ إلغاء", f"w_card|{oid}")))
    elif action == "w_commit":
        draft = view.get("wallet_preview")
        if not draft or draft[0] != args[0]:
            return
        view.pop("wallet_preview", None)
        _, command, oid, value, txid = draft
        if command == "rate":
            store.set_setting("wallet_syp_per_usd", str(value), uid)
            panel.screen(bot, chat, msg, f"تم حفظ السعر: 1 USD = {value:,} SYP.\n"
                         "تظهر الأسعار المحدثة بالليرة عند إعادة فتح صفحة المنتج.",
                         panel.menu(panel.button("⬅️ لوحة الأدمن", "home")))
            return
        row = wallet.get(oid)
        if not row:
            return
        if command == "contact":
            sent = panel.notify(bot, row["user_id"], f"💬 #{oid}\n{escape(value)}",
                                reply_markup=menu(button("عرض الطلب / View request", f"w_status|{oid}")))
            admin_ui(panel, bot, chat, msg, oid, "تم إرسال الرسالة إلى العميل." if sent else "تعذّر إرسال الرسالة.")
            return
        decision = "approved" if command == "approve" else "rejected"
        result = wallet.decide(oid, uid, decision, received=value if command == "approve" else None,
                               verified=txid, reason=value if command == "reject" else "")
        if result == decision:
            updated = wallet.get(oid)
            lang = db.get_user(row["user_id"])["lang"]
            sent = panel.notify(bot, row["user_id"], summary(updated, lang),
                                reply_markup=menu(button("المحفظة / Wallet", "wallet")))
            note = "تم تسجيل القرار." if sent else "تم تسجيل القرار وتعذّر إشعار العميل."
        else:
            note = {"mismatch": "المبلغ المستلم يختلف عن مبلغ الطلب. لا يزال الطلب قيد المراجعة.", "duplicate": "مرجع التحويل معتمد سابقاً.",
                    "hash": "معرّف المعاملة غير صحيح أو غير مطابق.", "closed": "سبق اتخاذ قرار بشأن الطلب."}.get(result, "تعذّر تسجيل القرار.")
        admin_ui(panel, bot, chat, msg, oid, note)


def admin_text(panel, bot, message, view):
    uid, raw = message.from_user.id, message.text.strip()
    command, oid = view["wallet_action"], view["oid"]
    txid = None
    value = raw
    if command == "approve":
        row = wallet.get(oid)
        if not row or row["state"] != "pending":
            return
        fields = raw.split()
        need_hash = row["method"] == "usdt"
        value = wallet.parse_amount(fields[0], row["currency"]) if fields else None
        txid = wallet.tx_hash(fields[1]) if need_hash and len(fields) == 2 else None
        initial_hash = wallet.tx_hash(row["reference"]) if need_hash else None
        if len(fields) != (2 if need_hash else 1) or value != Decimal(row["amount"]) or (
            need_hash and (not txid or initial_hash and txid != initial_hash)
        ):
            bot.send_message(uid, "راجع المبلغ المستلم ومعرّف المعاملة إن كان مطلوباً. لا يزال الطلب قيد المراجعة.")
            return
        preview = f"اعتماد شحن #{oid} وإضافة <b>{Decimal(row['credit']):.2f} USD</b>؟"
    elif command == "rate":
        value = wallet.parse_amount(raw, "USD")
        if value is None or value > Decimal("1000000000"):
            bot.send_message(uid, "أرسل سعر صرف موجباً برقم واضح.")
            return
        preview = f"اعتماد 1 USD = {value:,} SYP؟\nمثال: منتج بـ5 USD {wallet.syp_label(5, value, 'ar')}."
    else:
        if not 1 <= len(raw) <= 350:
            bot.send_message(uid, "أرسل نصاً بين 1 و350 حرفاً.")
            return
        preview = f"#{oid} · {'رفض الطلب' if command == 'reject' else 'مراسلة العميل'}\n{escape(raw)}"
    token = token_hex(8)
    view.pop("action", None)
    view["wallet_preview"] = (token, command, oid, value, txid)
    panel.screen(bot, uid, None, preview, panel.menu(panel.button("تأكيد", f"w_commit|{token}"),
                 panel.button("إلغاء", f"w_card|{oid}" if oid else "w_queue|0|1")))
