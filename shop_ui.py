from locales import ui_text
import re
from datetime import datetime, timezone
import time
from decimal import Decimal
from html import escape
from secrets import token_hex
from threading import Lock

from telebot import types

import api
import promotions
import catalog
import checkout
import cv_intake
import suppliers
import commerce_db as store
import wallet
import database as db
from locales import t

from locales import SHOP_TEXT as TEXT, SHOP_STATES as STATES

LOCK = Lock()


def tr(lang, key, **values):
    return TEXT[key][0 if lang == "ar" else 1].format(**values)


def label(lang, state):
    return STATES.get(state, (state, state))[0 if lang == "ar" else 1]


def date(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if value else "—"


def button(name, callback):
    return types.InlineKeyboardButton(name, callback_data=callback)


def menu(*buttons):
    markup = types.InlineKeyboardMarkup(row_width=1)
    if buttons:
        markup.add(*buttons)
    return markup


def render(bot, chat, msg, text, markup):
    if msg is None:
        bot.send_message(chat, text, reply_markup=markup)
    else:
        from admin_panel import screen
        screen(bot, chat, msg, text, markup)


def product(pid, lang):
    return next((item for item in api.get_products(lang) if item["id"] == pid), None)


def search_results(lang, text):
    ar = api.listing_products("ar")
    en = api.listing_products("en")
    alternate = {item["id"]: item for item in (en if lang == "ar" else ar)}
    query = catalog.normalize(text)
    results = []
    for item in ar if lang == "ar" else en:
        names = [item["name"], alternate.get(item["id"], {}).get("name", "")]
        brands = [catalog.BRANDS.get(catalog.brand_for(name), ("", ()))[0] for name in names]
        aliases = [alias for name in names for alias in catalog.BRANDS.get(catalog.brand_for(name), ("", ()))[1]]
        if query and any(query in catalog.normalize(name) for name in [*names, *brands, *aliases, item["id"]]):
            results.append(item)
    return results


def details(item, lang):
    mode = (item.get("input") or {}).get("type")
    methods = {
        "email": ("دعوة إلى البريد المقدّم", "Invitation to the specified email"),
        "activation_identifier": ("تفعيل أو شحن على المعرّف المقدّم", "Activation or top-up for the specified account ID"),
        "quantity": ("حسب وصف الباقة وتعليمات المورد", "According to the plan and supplier instructions"),
    }
    method = methods.get(mode, ("حسب وصف الباقة", "According to the plan description"))[lang != "ar"]
    keys = {"duration": ("المدة", "Duration"), "region": ("البلد", "Region"), "device": ("الجهاز", "Device"),
            "requirements": ("متطلبات الحساب", "Requirements"), "warranty": ("الضمان", "Warranty"),
            "eta": ("وقت التسليم", "Delivery time"), "method": ("طريقة الاستلام", "Delivery method")}
    meta = item.get("shop_meta", {})
    lines = [(ui_text(lang, "shop_ui_delivery_method")) + ": " + escape(meta.get("method") or method)]
    for key, labels in keys.items():
        if key == "method":
            continue
        value = meta.get(key) or item.get(key)
        if isinstance(value, (str, int)) and value:
            lines.append(f"{labels[lang != 'ar']}: {escape(str(value)[:180])}")
    lines.append(tr(lang, "detail_missing"))
    return "\n".join(lines)


def cv_active():
    from design_studio import active
    return active("cv")


def begin(bot, chat, msg, uid, pid, lang, states):
    back = "ds" if pid == "cv" else "main"
    if pid == "cv":
        states.pop(uid, None)
        existing = store.cv_open(uid)
        if existing:
            show_order(bot, chat, msg, uid, existing["id"], lang, support_url())
            return
        if not cv_active():
            text = ("هذه الخدمة قيد التطوير. يمكنك طلب إشعار عند توفرها." if lang == "ar"
                    else "This service is paused. Get notified when it returns.")
            render(bot, chat, msg, text, menu(button("🔔", "ds|watch|cv"), button(t(lang, "btn_back"), back)))
            return
    if not (checkout.is_enabled() or wallet.enabled()):
        render(bot, chat, msg, tr(lang, "checkout_off"), menu(button(t(lang, "btn_back"), back)))
        return
    item = product(pid, lang) if pid != "cv" else None
    if pid != "cv" and not item:
        render(bot, chat, msg, t(lang, "unavail"), menu(button(t(lang, "btn_back"), "cats")))
        return
    if pid == "cv" and store.edits().get("cv", {}).get("hidden"):
        render(bot, chat, msg, tr(lang, "checkout_off"), menu(button(t(lang, "btn_back"), back)))
        return
    state = {"mode": "checkout", "pid": pid, "qty": catalog.order_quantity(item) if item else 1,
             "name": item["name"] if item else t(lang, "cv_name"),
             "input": item.get("input", {}) if item else {}, "awaiting": None}
    state["category"] = item.get("smart_cat","") if item else ""
    states[uid] = state
    spec = state["input"]
    if spec.get("type") == "quantity" and spec.get("max_quantity", state["qty"]) > state["qty"]:
        state["awaiting"] = "qty"
        msg_text = f"{tr(lang, 'qty')}: {state['qty']}–{spec['max_quantity']}"
        render(bot, chat, msg, msg_text, menu(button(t(lang, "btn_back"), f"prod|{pid}")))
    elif spec.get("type") in {"activation_identifier", "email"}:
        state["awaiting"] = "target"
        render(bot, chat, msg, t(lang, "ask_email" if spec["type"] == "email" else "ask_id", name=state["name"]),
               menu(button(t(lang, "btn_back"), f"prod|{pid}")))
    else:
        review(bot, chat, msg, uid, lang, state)


def discounted(uid,state,cost):
    import promotions
    pid,qty=state["pid"],state["qty"]
    stars,usd=checkout_prices(pid,qty,cost)
    base=Decimal("5") if pid=="cv" else (api.calc_price(cost,pid,qty) if cost is not None else None)
    if base is None or base<=0: return stars,usd,None
    cat=state.get("category","")
    sale,adjusted,code=promotions.prices(uid,pid,cat,base,stars,cost)
    return adjusted,sale if usd is not None else None,(base,stars,cat,sale,adjusted,code)


def review(bot, chat, msg, uid, lang, state, changed=False):
    pid, quantity = state["pid"], state["qty"]
    cost = checkout.supplier_quote(pid, quantity) if pid != "cv" else None
    stars, usd, promo = discounted(uid,state,cost)
    rate = wallet.rate_for("SYP") if usd is not None else None
    state.update(stars=stars, usd=usd, cost=cost, syp_rate=rate, awaiting=None, review_token=token_hex(8))
    token = state["review_token"]
    markup = menu()
    if stars is not None:
        markup.add(button(tr(lang, "pay"), f"star_invoice|{token}"))
    if usd is not None:
        markup.add(button(tr(lang, "wallet_pay"), f"wallet_buy|{token}"))
    markup.add(button("🎟 كوبون / Coupon","x|coupon"))
    markup.add(button(t(lang, "btn_back"), "ds") if pid == "cv" else button(tr(lang, "edit"), f"buy|{pid}"),
               button(t(lang, "btn_main"), "main"))
    if stars is None and usd is None:
        render(bot, chat, msg, tr(lang, "changed") if changed else tr(lang, "checkout_off"), markup)
        return
    text = f"🧾 <b>{tr(lang, 'review')}</b>\n{escape(state['name'])}\n{tr(lang, 'qty')}: {quantity}"
    if state.get("target"):
        text += f"\n{tr(lang, 'target')}: <code>{escape(state['target'])}</code>"
    if pid == "cv":
        cv_meta = store.edits().get("cv", {})
        text += f"\n{tr(lang, 'cv_intro')}"
        if cv_meta.get("description"):
            text += "\n" + escape(cv_meta["description"][:700])
        text += "\n" + details({"shop_meta": cv_meta}, lang)
    if changed:
        text += f"\n⚠️ {tr(lang, 'changed')}"
    if promo and promo[0]!=promo[3] and (usd is not None or promo[1]!=promo[4]):
        text+=f"\n🏷 <s>{promo[0]:.2f} USD</s> → {promo[3]:.2f} USD"
    prices = []
    if stars is not None:
        prices.append(f"{stars} ⭐")
    if usd is not None:
        equivalent = wallet.syp_label(usd, rate, lang)
        prices.append(f"{usd:.2f} USD" + (f" ({equivalent})" if equivalent else ""))
    text += f"\n\n💳 <b>{tr(lang, 'price')}</b>\n<blockquote>{' · '.join(prices)}</blockquote>"
    if stars is not None:
        text += f"\n\n<blockquote>⚠️ {tr(lang, 'warn')}</blockquote>"
    else:
        warning = (ui_text(lang, "shop_ui_confirm_safely"))
        text += f"\n\n<blockquote>⚠️ {warning}</blockquote>"
    render(bot, chat, msg, text, markup)


def checkout_prices(pid, quantity, cost):
    usd = Decimal("5.00") if pid == "cv" else (api.calc_price(cost, pid, quantity) if cost is not None else None)
    stars = checkout.stars_price(pid, usd, quantity, cost) if checkout.is_enabled() else None
    return stars, usd if wallet.enabled() and usd is not None and usd > 0 else None


def checkout_text(bot, message, lang, state):
    raw = message.text.strip()
    spec = state["input"]
    if state["awaiting"] == "qty":
        if not re.fullmatch(r"\d{1,7}", raw) or not spec["min_quantity"] <= int(raw) <= spec["max_quantity"]:
            bot.send_message(message.chat.id, t(lang, "invalid_input"))
            return
        state["qty"] = int(raw)
    elif state["awaiting"] == "target":
        limits = spec.get("activation_identifier") or {}
        valid = limits.get("min_length", 2) <= len(raw) <= limits.get("max_length", 500)
        if spec["type"] == "email":
            valid = valid and bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw))
        if not valid or any(ch.isspace() for ch in raw):
            bot.send_message(message.chat.id, t(lang, "invalid_input"))
            return
        state["target"] = raw
    else:
        return
    review(bot, message.chat.id, None, message.from_user.id, lang, state)


def issue(bot, chat, msg, uid, lang, states, token, method="stars"):
    with LOCK:
        state = states.get(uid, {})
        if state.get("mode") != "checkout" or state.get("busy") or state.get("review_token") != token:
            return
        selected = state.get("usd" if method == "wallet" else "stars")
        if selected is None or not (wallet.enabled() if method == "wallet" else checkout.is_enabled()):
            return
        state["busy"] = True
    try:
        pid, qty = state["pid"], state["qty"]
        if pid == "cv" and (not cv_active() or store.cv_open(uid) or store.edits().get("cv", {}).get("hidden")):
            render(bot, chat, msg, tr(lang, "changed"), menu(button(t(lang, "btn_back"), "main")))
            return
        if pid != "cv" and not product(pid, lang):
            render(bot, chat, msg, tr(lang, "changed"), menu(button(t(lang, "btn_back"), "cats")))
            return
        cost = checkout.supplier_quote(pid, qty) if pid != "cv" else None
        stars, usd, promo = discounted(uid,state,cost)
        reservation=(*promo,method) if promo else None
        value = usd if method == "wallet" else stars
        rate = wallet.rate_for("SYP") if method == "wallet" else None
        rate_changed = method == "wallet" and rate != state["syp_rate"]
        if value != selected or cost != state["cost"] or rate_changed or states.get(uid) is not state:
            if states.get(uid) is state:
                review(bot, chat, msg, uid, lang, state, changed=True)
            return
        body = suppliers.order_body(pid, qty, cost, state) if cost is not None else None
        cost_text = str(cost) if cost is not None else None
        if method == "wallet":
            row, created = store.wallet_purchase(uid, pid, state["name"], qty, usd, cost_text, body,
                                                 f"wp-{token}", syp_rate=rate,promo=reservation)
            if row is None:
                if rate != wallet.rate_for("SYP"):
                    review(bot, chat, msg, uid, lang, state, changed=True)
                    return
                existing = store.cv_open(uid) if pid == "cv" else None
                if existing:
                    show_order(bot, chat, msg, uid, existing["id"], lang)
                else:
                    back = "cv_offer" if pid == "cv" else f"prod|{pid}"
                    render(bot, chat, msg, tr(lang, "wallet_low"),
                           menu(button("💳 " + (ui_text(lang, "shop_ui_add_funds")), "w_methods"),
                                button(t(lang, "btn_back"), back)))
                return
            states.pop(uid, None)
            if created and pid == "cv":
                from admin_panel import ADMIN_ID, notify

                notify(bot, ADMIN_ID, f"📄 طلب سيرة ذاتية مدفوع #{row['id']}",
                       reply_markup=menu(button("فتح الطلب", f"adm|opview|{row['id']}")))
        else:
            row = store.invoice(uid, pid, state["name"], qty, stars, cost_text, body,promo=reservation,review_token=token)
            states.pop(uid, None)
            checkout.send_invoice(bot, row, lang == "ar")
        show_order(bot, chat, msg, uid, row["id"], lang)
    except promotions.PriceChanged:
        review(bot,chat,msg,uid,lang,state,changed=True)
    finally:
        state.pop("busy", None)


def show_orders(bot, chat, msg, uid, lang, page):
    results = store.orders(uid=uid, page=page)
    keys = menu(*(button(f"#{row['id']} · {row['name'][:30]} · {label(lang, row['state'])}", f"star_order|{row['id']}")
                  for row in results[:8]))
    if page:
        keys.add(button(t(lang, "btn_prev"), f"star_orders|{page - 1}"))
    if len(results) > 8:
        keys.add(button(t(lang, "btn_next"), f"star_orders|{page + 1}"))
    keys.add(button(tr(lang, "old_orders"), "legacy_orders"), button(t(lang, "btn_main"), "main"))
    render(bot, chat, msg, tr(lang, "stars_orders") if results else t(lang, "no_purchases"), keys)


def support_url():
    from config import SUPPORT_USERNAME

    return f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}"


def show_order(bot, chat, msg, uid, oid, lang, support_link=None):
    row = store.order(oid)
    if not row or row["user_id"] != uid:
        return
    markup = menu(button("🔄 " + (ui_text(lang, "shop_ui_refresh_status")), f"star_order|{oid}"),
                  button(tr(lang, "support"), f"order_help|{oid}"),
                  button(tr(lang, "stars_orders"), "star_orders|0"))
    if row["state"]=="delivered" and row["product_id"]!="cv":
        markup.add(button("🔄 اطلب مجدداً / Reorder",f"x|reorder|{oid}"))
    markup.add(button("💬 سجل الدعم / Messages",f"x|ticket|{oid}"))
    if row["wallet_amount"] is not None:
        paid_label = f"{row['wallet_amount']} USD"
        if row["wallet_syp_rate"] is not None:
            paid_label += f" ({wallet.syp_label(row['wallet_amount'], Decimal(row['wallet_syp_rate']), lang)})"
    else:
        paid_label = f"{row['stars']} ⭐"
    text = (f"<b>#{oid} · {escape(row['name'])}</b>\n{row['quantity']} × · {paid_label}\n"
            f"{label(lang, row['state'])}\n{date(row['updated'])}")
    if row["state"] == "invoice":
        if row["expires"] < int(time.time()):
            text += ui_text(lang, "shop_ui_invoice_expired")
        text += f"\n\n{tr(lang, 'wait')}"
    if row["supplier_quantity"] is not None:
        title = ui_text(lang, "shop_ui_quantity_delivered")
        text += f"\n{title}: {row['supplier_quantity']}/{row['quantity']}"
    if row["delivery"] and row["state"] in {"delivered", "partial", "partial_closed"}:
        text += f"\n\n<code>{escape(row['delivery'][:1700])}</code>"
        if len(row["delivery"]) > 1700:
            markup.add(button("🔑 " + (ui_text(lang, "shop_ui_full_delivery")), f"star_delivery|{oid}"))
    if row["product_id"] == "cv" and row["state"] in {"cv_details", "cv_work"}:
        text += f"\n\n{tr(lang, 'cv_intro')}"
        if row["state"] == "cv_details":
            if cv_intake.submission(oid):
                text += f"\n\n{t(lang, 'cv_app_done')}"
            elif cv_intake.webapp_url():
                markup.add(button(t(lang, "cv_app_button"), f"cvapp|{oid}"))
        markup.add(types.InlineKeyboardButton(t(lang, "cv_support"), url=support_link or support_url()))
    ticket = store.get_ticket(oid)
    if ticket and ticket["reply"]:
        text += f"\n\n💬 {escape(ticket['reply'][:700])}"
    render(bot, chat, msg, text, markup)
