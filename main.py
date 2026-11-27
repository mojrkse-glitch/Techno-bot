from locales import ui_text
import math
import time
import hashlib
import sqlite3
from decimal import InvalidOperation
from urllib.parse import urlencode
from collections import OrderedDict
from threading import Lock
from types import SimpleNamespace
import design_studio
import storefront
import operations
from platform_db import rows,write
from sessions import Sessions
import re
import socket
from decimal import Decimal
from html import escape

import telebot
import urllib3.util.connection as urllib3_connection
from telebot import types
from config import (ADMIN_ID, BOT_TOKEN, FORCE_SUB_CHANNEL,
                    FORCE_SUB_LINK, JRK_API_KEY, SUPPORT_USERNAME)
from locales import TEXTS, t
import database as db
import api
import admin_panel
import catalog
import checkout
import commerce_db as store
import cv_intake
import shop_ui
import suppliers
import ui_style
import wallet
import wallet_ui

bot = telebot.TeleBot(BOT_TOKEN or "0:unconfigured", parse_mode="HTML")
user_states = Sessions()
SUPPORT_URL = f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}"


def is_subscribed(user_id):
    try:
        member = bot.get_chat_member(FORCE_SUB_CHANNEL, user_id)
    except (telebot.apihelper.ApiException, OSError):
        return None
    return member.status in {"member", "administrator", "creator"} or (
        member.status == "restricted" and getattr(member, "is_member", False)
    )


def send_force_sub(chat_id, lang, message_id=None):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t(lang, "btn_join"), url=FORCE_SUB_LINK))
    markup.add(types.InlineKeyboardButton(t(lang, "btn_check_sub"), callback_data="check_sub"))
    text = t(lang, "force_sub", channel=FORCE_SUB_CHANNEL)
    if message_id is None:
        bot.send_message(chat_id, text, reply_markup=markup)
    else:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup)


def referral_id(text, user_id):
    args = text.split()
    if len(args) == 2 and re.fullmatch(r"[0-9]{1,19}", args[1]):
        ref = int(args[1])
        if 0 < ref < 2**63 and ref != user_id:
            return ref
    return None

def main_menu_markup(lang):
    ar=lang=="ar"
    m = types.InlineKeyboardMarkup(row_width=2)
    B=shop_ui.button
    m.add(B(ui_text(lang, "main_today_offers"),"x|offers"))
    m.row(B(t(lang,"btn_cats"),"cats"),B(shop_ui.tr(lang,"search"),"search"))
    m.row(B(ui_text(lang, "main_design_studio"),"ds"),B(ui_text(lang, "main_plan_finder"),"x|wizard"))
    m.row(B(wallet_ui.name(lang),"wallet"),B(t(lang,"btn_purchases"),"purchases"))
    m.row(B(ui_text(lang, "main_coupon_referrals"),"referral"),B(ui_text(lang, "main_favorites"),"x|favorites"))
    m.row(B(ui_text(lang, "main_help_faq"),"x|faq"),B(t(lang,"btn_profile"),"profile"))
    m.add(B(t(lang,"btn_lang"),"lang_menu"))
    return m

def send_main_menu(chat_id, user, msg_id=None):
    u = db.get_user(user.id)
    equivalent=wallet.syp_label(u["balance"],wallet.rate_for("SYP"),u["lang"])
    txt=f"👋 <b>{escape(user.first_name or 'User')} · Techo Pay</b>\n💳 {u['balance']:.2f} USD {equivalent or ''}\n{storefront.loyalty(u)}\n👥 "+str(db.get_referrals_count(user.id))
    mk = main_menu_markup(u["lang"])
    if msg_id:
        bot.edit_message_text(txt, chat_id, msg_id, reply_markup=mk)
    else:
        bot.send_message(chat_id, txt, reply_markup=mk)

@bot.message_handler(commands=["start"])
@user_states.handler
def cmd_start(message):
    user = message.from_user
    if message.chat.type != "private":
        return
    user_states.pop(user.id, None)
    account = db.get_user(user.id, user.username or "", referral_id(message.text, user.id), "ar" if str(getattr(user,"language_code","") or "").startswith("ar") else "en")
    if account["is_banned"]: return
    parts=message.text.split(maxsplit=1)
    deep=parts[1] if len(parts)>1 else ""
    if re.fullmatch(r"(prod_|promo_)[A-Za-z0-9_-]{1,58}|design",deep): user_states[user.id]={"mode":"deep","value":deep}
    membership = is_subscribed(user.id)
    if membership is None:
        bot.send_message(message.chat.id, shop_ui.tr(account["lang"], "membership_error"))
        return
    if not membership:
        send_force_sub(message.chat.id, account["lang"])
        return
    if not deep_open(user,account["lang"]):
        send_main_menu(message.chat.id, user)


def deep_open(user,lang):
    state=user_states.get(user.id,{})
    if state.get("mode")!="deep": return False
    deep=state["value"]
    user_states.pop(user.id,None)
    if deep.startswith("promo_"):
        select_coupon(user.id,deep[6:],lang)
    elif deep=="design":
        design_studio.callback(bot,user.id,None,"ds",lang,user_states)
    else:
        pid=deep[5:]
        show_product(user.id,None,pid,lang)
    return True


def select_coupon(uid,code,lang):
    code=code.strip().upper()
    if not rows("SELECT code FROM coupons WHERE code=? AND expires>unixepoch()",(code,)):
        bot.send_message(uid,ui_text(lang, "main_invalid_coupon"))
        return False
    write("INSERT OR REPLACE INTO selected_coupon VALUES(?,?)",(uid,code))
    bot.send_message(uid,"🎟 "+escape(code)+" · "+(ui_text(lang, "main_coupon_selected")))
    return True


@bot.inline_handler(func=lambda query: True)
def inline_search(query):
    uid=query.from_user.id
    if limited(uid,"#inline",8): return bot.answer_inline_query(query.id,[],cache_time=2,is_personal=True)
    if db.get_user(uid)["is_banned"]: return bot.answer_inline_query(query.id,[],cache_time=10,is_personal=True)
    lang=db.get_user(uid)["lang"]
    found=shop_ui.search_results(lang,query.query[:80]) if query.query.strip() else api.get_products(lang)
    username=bot.get_me().username
    items=[]
    for r in found[:15]:
        link=f"https://t.me/{username}?start=prod_{r['id']}"
        items.append(types.InlineQueryResultArticle(hashlib.sha256(r["id"].encode()).hexdigest(),r["name"],
            types.InputTextMessageContent(escape(r["name"])),reply_markup=shop_ui.menu(types.InlineKeyboardButton("🛍 Open / فتح",url=link))))
    bot.answer_inline_query(query.id,items,cache_time=30,is_personal=True)


@bot.message_handler(commands=["admin"])
@user_states.handler
def cmd_admin(message):
    if message.chat.type=="private" and message.from_user.id!=ADMIN_ID and design_studio.staff(message.from_user.id):
        operations.callback(bot,message.from_user.id,None,"op|home",user_states)
        return
    if not admin_panel.allowed(message.from_user, message.chat):
        return
    user_states.pop(message.from_user.id, None)
    admin_panel.home(bot, message.chat.id)


@bot.message_handler(commands=["paysupport"])
@user_states.handler
def payment_support(message):
    if message.chat.type != "private":
        return
    uid = message.from_user.id
    lang = db.get_user(uid)["lang"]
    user_states.pop(uid, None)
    shop_ui.show_orders(bot, message.chat.id, None, uid, lang, 0)
    bot.send_message(message.chat.id,
                     ui_text(lang, "main_payment_support"),
                     reply_markup=shop_ui.menu(types.InlineKeyboardButton(t(lang, "btn_support"), url=SUPPORT_URL)))


@bot.pre_checkout_query_handler(func=lambda query: True)
def checkout_query(query):
    checkout.precheck(bot, query)


@bot.message_handler(content_types=["web_app_data"])
def cv_web_app_data(message):
    cv_intake.handle(bot, message)


@bot.message_handler(content_types=["successful_payment", "refunded_payment"])
def payment_result(message):
    if message.chat.type != "private":
        return
    payment = message.successful_payment or message.refunded_payment
    uid = message.from_user.id
    if message.refunded_payment:
        store.update_refund(payment.telegram_payment_charge_id, uid, payment.total_amount,
                            payment.currency, payment.invoice_payload)
        return
    oid = store.paid(payment.invoice_payload, uid, payment.total_amount, payment.currency,
                     payment.telegram_payment_charge_id)
    if oid:
        lang = db.get_user(uid)["lang"]
        bot.send_message(message.chat.id, shop_ui.tr(lang, "paid_notice", id=oid),
                         reply_markup=shop_ui.menu(shop_ui.button(shop_ui.tr(lang, "stars_orders"), f"star_order|{oid}")))
        if store.order(oid)["product_id"] == "cv":
            admin_panel.notify(bot, ADMIN_ID, f"📄 طلب سيرة ذاتية مدفوع #{oid}",
                               reply_markup=admin_panel.menu(admin_panel.button("فتح الطلب", f"opview|{oid}")))


_recent=OrderedDict()
_rate_lock=Lock()
def limited(uid,action,maximum=4):
    now=time.monotonic()
    with _rate_lock:
        key=(uid,action)
        start,count=_recent.pop(key,(now,0))
        if now-start>2: start,count=now,0
        _recent[key]=(start,count+1)
        if len(_recent)>4096: _recent.popitem(last=False)
        return count>=maximum


@bot.callback_query_handler(func=lambda c: True)
@user_states.handler
def callbacks(call):
    if not call.message or call.message.chat.type != "private":
        bot.answer_callback_query(call.id)
        return
    uid = call.from_user.id
    data = call.data or ""
    if db.get_user(uid)["is_banned"]:
        bot.answer_callback_query(call.id)
        return
    if limited(uid,data) or limited(uid,"#buttons",20):
        bot.answer_callback_query(call.id)
        return
    if data.startswith("sortlist|"):
        _,sort_key,route=data.split("|",2)
        if sort_key not in ("price","sales","offers","default"): return
        write("INSERT OR REPLACE INTO browse_sort VALUES(?,?)",(uid,sort_key))
        data=route
    if data.startswith("op|"):
        bot.answer_callback_query(call.id)
        try: operations.callback(bot,uid,call.message.message_id,data,user_states)
        except (ValueError,InvalidOperation,sqlite3.IntegrityError):
            bot.send_message(uid,"تعذّر اعتماد الإدخال. راجع القيم وأعد المحاولة.")
        return
    if data.startswith("adm|"):
        admin_panel.handle_callback(bot, call, user_states)
        return
    # Keep an active private admin prompt when an older customer button is tapped.
    active = user_states.get(uid) or {}
    if active.get("mode") == "admin" and active.get("action") and data != "main":
        bot.answer_callback_query(call.id, "أكمل خطوة الإدارة الحالية أو اضغط إلغاء.", show_alert=True)
        return
    if active.get("mode") == "admin":
        user_states.pop(uid, None)
    u = db.get_user(uid)
    lang = u["lang"]
    membership = is_subscribed(uid)
    post_purchase = data in {"purchases", "legacy_orders", "wallet"} or data.startswith(
        ("star_orders|", "star_order|", "order_help|", "delivery|", "star_delivery|", "w_status|", "w_list|", "cvapp|"))
    if membership is None and not post_purchase:
        bot.answer_callback_query(call.id, shop_ui.tr(lang, "membership_error"), show_alert=True)
        return
    if not membership and not post_purchase:
        if active.get("mode")!="deep": user_states.pop(uid, None)
        if data == "check_sub":
            bot.answer_callback_query(call.id, t(lang, "not_subbed"), show_alert=True)
        else:
            bot.answer_callback_query(call.id)
            send_force_sub(call.message.chat.id, lang)
        return

    if data not in {"refresh", "noop"} and not data.startswith(("buy|", "prod|")):
        bot.answer_callback_query(call.id)

    if data=="ds" or data.startswith("ds|"):
        if data.startswith("ds|card|"): user_states.pop(uid,None)
        design_studio.callback(bot,uid,call.message.message_id,data,lang,user_states)
        return
    if data.startswith("x|"):
        storefront.callback(bot,uid,call.message.message_id,data,lang,user_states)
        return
    if data.startswith("quick|"):
        _,nonce,raw=data.split("|")
        state=user_states.get(uid,{})
        if state.get("step")=="amount" and state.get("nonce")==nonce:
            wallet_ui.collect(bot,SimpleNamespace(from_user=call.from_user,text=raw),lang,state,user_states)
        return
    if data == "check_sub":
        if deep_open(call.from_user,lang): return
        send_main_menu(call.message.chat.id, call.from_user, call.message.message_id)

    elif data == "main":
        user_states.pop(uid, None)
        send_main_menu(call.message.chat.id, call.from_user, call.message.message_id)

    elif data == "lang_menu":
        m = types.InlineKeyboardMarkup(row_width=2)
        m.add(
            types.InlineKeyboardButton("🇸🇾 العربية", callback_data="setlang_ar"),
            types.InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
            types.InlineKeyboardButton("🇻🇳 Tiếng Việt", callback_data="setlang_vi"),
            types.InlineKeyboardButton("🇮🇳 हिन्दी", callback_data="setlang_hi"),
            types.InlineKeyboardButton("🇫🇷 Français", callback_data="setlang_fr")
        )
        m.add(types.InlineKeyboardButton(t(lang, "btn_main"), callback_data="main"))
        bot.edit_message_text(t(lang, "choose_lang"), call.message.chat.id, call.message.message_id, reply_markup=m)

    elif data.startswith("setlang_"):
        new_lang = data.split("_")[1]
        if new_lang not in TEXTS:
            return
        db.set_lang(uid, new_lang)
        send_main_menu(call.message.chat.id, call.from_user, call.message.message_id)

    elif data == "refresh":
        api.get_products(lang, force=True)
        bot.answer_callback_query(call.id, t(lang, "refreshed"), show_alert=True)

    elif data == "cats" or re.fullmatch(r"brands\|[0-9]+|cat\|[0-9]+\|[0-9]+", data):
        user_states.pop(uid, None)
        page = int(data.split("|")[1]) if data.startswith("brands|") else 0
        show_brands(call, lang, page)

    elif data == "types":
        user_states.pop(uid, None)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(shop_ui.button("🔔 "+(ui_text(lang, "main_unavailable_products")),"x|stock"))
        for category in api.get_categories(lang):
            markup.add(types.InlineKeyboardButton(t(lang, f"category_{category}"), callback_data=f"cat|{category}|0"))
        markup.add(types.InlineKeyboardButton(t(lang, "back_brands"), callback_data="cats"))
        bot.edit_message_text(t(lang, "cats_title"), call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif re.fullmatch(r"cat\|(all|games|ai|design|streaming|other)\|[0-9]+", data):
        user_states.pop(uid, None)
        _, category, page = data.split("|")
        products = api.listing_products(lang)
        if category != "all":
            products = [item for item in products if item["smart_cat"] == category]
        parent = "cats" if category == "all" else "types"
        show_products(call, lang, products, f"cat|{category}", int(page), parent=parent)

    elif re.fullmatch(r"brand\|[a-z0-9_]+\|[0-9]+\|[0-9]+", data):
        user_states.pop(uid, None)
        _, brand, page, number = data.split("|")
        if brand in catalog.BRANDS or brand == catalog.OTHER_BRAND:
            products = brand_groups(lang).get(brand, [])
            show_products(call, lang, products, f"brand|{brand}", int(page),
                          parent=f"brands|{number}", source_page=number, title=brand_label(lang, brand))

    elif data.startswith("prod|"):
        _, pid, *origin = data.split("|")
        if not origin or origin[0] != "searchpage":
            user_states.pop(uid, None)
        bot.answer_callback_query(call.id)
        show_product(uid,call.message.message_id,pid,lang,origin)

    elif data.startswith("buy|"):
        bot.answer_callback_query(call.id)
        shop_ui.begin(bot, call.message.chat.id, call.message.message_id, uid, data.split("|", 1)[1], lang, user_states)

    elif data.startswith("cvapp|"):
        order_id = data.split("|", 1)[1]
        if order_id.isdigit():
            cv_intake.offer(bot, uid, call.message.chat.id, int(order_id), lang)

    elif data == "cv_offer" or data.startswith("cvok|"):
        shop_ui.begin(bot, call.message.chat.id, call.message.message_id, uid, "cv", lang, user_states)

    elif data.startswith(("star_invoice|", "wallet_buy|")):
        action, token = data.split("|", 1)
        method = "wallet" if action == "wallet_buy" else "stars"
        shop_ui.issue(bot, call.message.chat.id, call.message.message_id, uid, lang, user_states, token, method)

    elif data == "search":
        user_states[uid] = {"mode": "search"}
        shop_ui.render(bot, call.message.chat.id, call.message.message_id, shop_ui.tr(lang, "search_prompt"),
                       shop_ui.menu(shop_ui.button(t(lang, "btn_main"), "main")))

    elif re.fullmatch(r"searchpage\|[0-9]+", data):
        state = user_states.get(uid, {})
        if state.get("mode") == "search" and state.get("query"):
            products = shop_ui.search_results(lang, state["query"])
            show_products(call, lang, products, "searchpage", int(data.split("|")[1]), parent="search",
                          title=escape(state["query"]))

    elif data == "purchases" or data.startswith("star_orders|"):
        page = int(data.split("|")[1]) if "|" in data else 0
        shop_ui.show_orders(bot, call.message.chat.id, call.message.message_id, uid, lang, page)

    elif re.fullmatch(r"(star_order|order_help|star_delivery)\|[0-9]+", data):
        action, raw_oid = data.split("|")
        oid = int(raw_oid)
        row = store.order(oid)
        if not row or row["user_id"] != uid:
            return
        if action == "star_order":
            if user_states.get(uid, {}).get("mode") == "ticket":
                user_states.pop(uid, None)
            shop_ui.show_order(bot, call.message.chat.id, call.message.message_id, uid, oid, lang, SUPPORT_URL)
        elif action == "star_delivery":
            if row["state"] in {"delivered", "partial", "partial_closed"} and row["delivery"]:
                send_delivery(call.message.chat.id, row["delivery"])
        else:
            user_states[uid] = {"mode": "ticket", "oid": oid}
            shop_ui.render(bot, call.message.chat.id, call.message.message_id, shop_ui.tr(lang, "support_prompt"),
                           shop_ui.menu(shop_ui.button(t(lang, "btn_back"), f"star_order|{oid}")))

    elif data == "profile":
        orders_cnt, keys_cnt = db.get_user_stats(uid)
        txt = t(lang, "profile_text", uid=uid, name=call.from_user.first_name, bal=u["balance"], orders=orders_cnt, keys=keys_cnt, spent=u["total_spent"], refs=db.get_referrals_count(uid))
        txt+="\n"+storefront.loyalty(u)
        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(
            types.InlineKeyboardButton(t(lang, "btn_referral"), callback_data="referral"),
            types.InlineKeyboardButton(t(lang, "btn_vault"), callback_data="purchases"),
            types.InlineKeyboardButton(t(lang, "btn_main"), callback_data="main")
        )
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m)

    elif data == "referral":
        link = f"https://t.me/{bot.get_me().username}?start={uid}"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t(lang, "btn_back"), callback_data="profile"))
        markup.add(types.InlineKeyboardButton("📋",copy_text=types.CopyTextButton(link)),types.InlineKeyboardButton("📤",url="https://t.me/share/url?"+urlencode({"url":link,"text":"Techo Pay 🛍"})))
        markup.add(shop_ui.button("🎟 "+(ui_text(lang, "main_enter_coupon")),"x|coupon"))
        cfg=store.settings()
        reward=cfg.get("ref_fixed","0")+" USD" if Decimal(cfg.get("ref_fixed","0")) else cfg.get("ref_pct","0")+"%"
        bot.edit_message_text(f"🎁 <b>Techo Pay</b>\n{link}\n"+((ui_text(lang, "main_first_order_reward"))+reward), call.message.chat.id,
                              call.message.message_id, reply_markup=markup)

    elif data == "legacy_orders":
        orders = db.get_user_orders(uid)
        m = types.InlineKeyboardMarkup(row_width=1)
        if not orders:
            txt = t(lang, "no_purchases")
            m.add(types.InlineKeyboardButton(t(lang, "btn_all"), callback_data="cat|all|0"))
        else:
            lines = []
            for row in orders:
                if row["kind"] == "cv":
                    status = t(lang, f"cv_status_{row['status']}")
                    lines.append(f"📄 <b>#{row['id']} · {t(lang, 'cv_name')}</b> (${row['sell_price']}) — {status}")
                    if row["status"] in db.CV_OPEN_STATES:
                        m.add(types.InlineKeyboardButton(t(lang, "cv_support"), url=SUPPORT_URL))
                    continue
                if row["status"] in db.PENDING_STATES and row["jrk_order_id"]:
                    row = sync_order(row, lang)
                line = f"📦 <b>{escape(row['product_name'][:110])}</b> (${row['sell_price']}) — <code>{escape(row['status'])}</code>"
                if row["delivery"]:
                    line += f"\n🔑 <code>{escape(row['delivery'][:200])}</code>"
                    if len(row["delivery"]) > 200:
                        view_label = ui_text(lang, "main_view_delivery")
                        m.add(types.InlineKeyboardButton(f"{view_label} · #{row['id']}", callback_data=f"delivery|{row['id']}"))
                lines.append(line)
            txt = "\n\n".join(lines)
        m.add(types.InlineKeyboardButton(t(lang, "btn_main"), callback_data="main"))
        bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=m)

    elif data.startswith("delivery|"):
        order = next((row for row in db.get_user_orders(uid) if str(row["id"]) == data.split("|", 1)[1]), None)
        if order and order["delivery"]:
            send_delivery(call.message.chat.id, order["delivery"])

    elif data == "wallet" or data == "w_methods" or re.fullmatch(r"w_(method\|[a-z_]+|status\|[0-9]{1,18}|list\|[0-9]{1,6})", data):
        wallet_ui.customer(bot, call, lang, user_states)

    elif data.startswith("dep|"):
        wallet_ui.home(bot, call.message.chat.id, call.message.message_id, uid, lang)

    elif data == "noop":
        bot.answer_callback_query(call.id)

def show_product(uid,msg,pid,lang,origin=()):
    item = next((r for r in api.get_products(lang,include_unavailable=True) if r["id"]==pid and not r.get("shop_meta",{}).get("hidden")),None)
    if not item:
        shop_ui.render(bot,uid,msg,t(lang,"unavail"),shop_ui.menu(shop_ui.button("⬅️","cats")))
        return
    cost = checkout.money(item.get("price"))
    stars, usd = shop_ui.checkout_prices(pid, 1, cost)
    markup = shop_ui.menu(ui_style.styled(shop_ui.button(t(lang, "btn_buy"), f"buy|{pid}"), ui_style.SUCCESS))
    if item.get("availability")=="unavailable": markup=shop_ui.menu()
    extra=storefront.card(item,uid,lang,markup)
    markup.add(shop_ui.button(wallet_ui.name(lang), "wallet"))
    markup.add(shop_ui.button(t(lang, "btn_back"), "|".join(origin) if origin else "cats"))
    description = escape((item.get("description") or suppliers.describe(item))[:1800])
    count = item.get("stock_count")
    if item.get("availability") == "unavailable":
        stock_line = "\n🔴 " + ("نفدت الكمية حالياً" if lang == "ar" else "Out of stock")
    elif isinstance(count, int):
        stock_line = "\n🟢 " + (f"المتوفر: {count}" if lang == "ar" else f"In stock: {count}")
    else:
        stock_line = ""
    options = [display for display in (
        f"{stars} ⭐" if stars is not None else None,
        f"{usd:.2f} USD" if usd is not None else None,
    ) if display]
    equivalent = wallet.syp_label(usd, wallet.rate_for("SYP"), lang) if usd is not None else ""
    if equivalent:
        options.append(equivalent)
    price = f"{shop_ui.tr(lang, 'unit_price')}: {' · '.join(options)}" if options else shop_ui.tr(lang, "checkout_off")
    text = f"{item['icon']} <b>{escape(item['name'])}</b>\n{description}\n\n{shop_ui.details(item, lang)}\n\n{price}{stock_line}"
    photo=item.get("shop_meta",{}).get("photo_file_id")
    if photo: bot.send_photo(uid,photo)
    shop_ui.render(bot, uid,msg, text+"\n"+extra, markup)

def brand_groups(lang):
    products = api.listing_products(lang)
    alternate = api.listing_products("en") if lang == "ar" and any(
        catalog.brand_for(item["name"]) == catalog.OTHER_BRAND for item in products
    ) else ()
    return catalog.product_groups(products, alternate)


def brand_label(lang, brand):
    if brand == catalog.API_BRAND:
        return t(lang, "brands_api")
    if brand == catalog.OTHER_BRAND:
        return t(lang, "brands_other")
    return catalog.BRANDS[brand][0]


def page_bounds(count, per_page, page):
    total = max(1, math.ceil(count / per_page))
    page = min(page, total - 1)
    return page, total, slice(page * per_page, (page + 1) * per_page)


def page_buttons(markup, lang, page, total, route):
    if total < 2:
        return
    nav = []
    if page:
        nav.append(types.InlineKeyboardButton(t(lang, "btn_prev"), callback_data=route(page - 1)))
    nav.append(types.InlineKeyboardButton(f"{page + 1}/{total}", callback_data="noop"))
    if page + 1 < total:
        nav.append(types.InlineKeyboardButton(t(lang, "btn_next"), callback_data=route(page + 1)))
    markup.row(*nav)


def show_brands(call, lang, page):
    groups = brand_groups(lang)
    brands = [key for key in (*catalog.BRANDS, catalog.OTHER_BRAND) if key in groups]
    page, total, shown = page_bounds(len(brands), 15, page)
    markup = types.InlineKeyboardMarkup(row_width=3)
    row = []
    for brand in brands[shown]:
        button = types.InlineKeyboardButton(brand_label(lang, brand), callback_data=f"brand|{brand}|0|{page}")
        if brand in {catalog.API_BRAND, catalog.OTHER_BRAND}:
            if row:
                markup.row(*row)
                row = []
            markup.row(button)
        else:
            row.append(button)
            if len(row) == 3:
                markup.row(*row)
                row = []
    if row:
        markup.row(*row)
    page_buttons(markup, lang, page, total, lambda number: f"brands|{number}")
    markup.row(types.InlineKeyboardButton(t(lang, "btn_all"), callback_data="cat|all|0"),
               types.InlineKeyboardButton(t(lang, "browse_types"), callback_data="types"))
    markup.add(types.InlineKeyboardButton(t(lang, "btn_main"), callback_data="main"))
    text = t(lang, "brands_title")
    if not groups:
        text = t(lang, "brand_no_products")
    bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)


def show_products(call, lang, products, prefix, page, parent, source_page=None, title=None):
    uid=getattr(getattr(call,"from_user",None),"id",None) if hasattr(call,"message") else call.chat.id
    pref=rows("SELECT sorting FROM browse_sort WHERE uid=?",(uid,))
    mode=pref[0]["sorting"] if pref else "default"
    if mode=="price":
        products=sorted(products,key=lambda r:api.calc_price(r["price"],r["id"]) or Decimal("Infinity"))
    elif mode=="sales":
        counts={r["product_id"]:r["n"] for r in rows("SELECT product_id,count(*) n FROM checkout_orders WHERE state='delivered' GROUP BY product_id")}
        products=sorted(products,key=lambda r:-counts.get(r["id"],0))
    elif mode=="offers":
        products=[r for r in products if storefront.is_offer(r)]
    page, total, shown = page_bounds(len(products), 5, page)
    suffix = f"|{source_page}" if source_page is not None else ""

    def route(number):
        return f"{prefix}|{number}{suffix}"

    markup = types.InlineKeyboardMarkup(row_width=1)
    pricing = api.pricing_snapshot()
    settings = store.settings()
    syp_rate = wallet.rate_for("SYP") if wallet.enabled() else None
    for item in products[shown]:
        cost = checkout.money(item.get("price"))
        price = api.calc_price(cost, item["id"], pricing=pricing) if cost is not None else None
        stars = checkout.stars_price(item["id"], price, cost=cost, settings=settings) if checkout.is_enabled() else None
        label = (wallet.syp_label(price, syp_rate, lang) or f"{price:.2f} USD") if wallet.enabled() and price is not None else (
            f"{stars} ⭐" if stars is not None else "•")
        callback=f"prod|{item['id']}|{route(page)}"
        if len(callback.encode())>64: callback=f"prod|{item['id']}"
        markup.add(ui_style.availability_button(f"{item['icon']} {item['name']} — {label}{ui_style.stock_suffix(item, lang)}",
                                                callback, item.get("availability") != "unavailable"))
    markup.row(*(shop_ui.button(label,f"sortlist|{key}|{route(0)}") for label,key in (("💲 ↓","price"),("🏅","sales"),("🔥","offers"),("↺","default"))))
    page_buttons(markup, lang, page, total, route)
    back_label = {"types": "browse_types", "search": "btn_back"}.get(parent, "back_brands")
    markup.row(types.InlineKeyboardButton(t(lang, back_label), callback_data=parent),
               types.InlineKeyboardButton(t(lang, "btn_refresh"), callback_data="refresh"))
    markup.add(types.InlineKeyboardButton(t(lang, "btn_main"), callback_data="main"))
    text = t(lang, "catalog_title", count=len(products)) if products else t(lang, "brand_no_products")
    if title:
        text = f"<b>{title}</b>\n\n{text}"
    message = getattr(call, "message", call)
    bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=markup)


@bot.message_handler(content_types=["text","photo","document","video"],func=lambda m: m.chat.type == "private" and m.from_user.id in user_states)
@user_states.handler
def handle_states(message):
    uid = message.from_user.id
    state = user_states.get(uid)
    if not state or db.get_user(uid)["is_banned"] or limited(uid,"#messages",20):
        return
    if state["mode"] == "admin":
        if state.get("action"):
            admin_panel.handle_text(bot, message, state)
        return
    lang = db.get_user(uid)["lang"]
    raw = (message.text or "").strip()
    if state["mode"]=="operation_confirm": return
    if state["mode"]=="operation":
        operations.collect(bot,message,state,user_states)
        return
    if state["mode"]=="thread_reply" and not raw: return
    if state["mode"] in ("brief","design_reply"):
        if is_subscribed(uid): design_studio.collect(bot,message,state,user_states)
        return
    if state["mode"]=="review_comment":
        if 0<len(raw)<=350:
            write("UPDATE product_reviews SET comment=? WHERE oid=? AND uid=?",(raw,state["oid"],uid))
            user_states.pop(uid,None)
            bot.send_message(uid,"✅")
        return
    if state["mode"]=="thread_reply":
        oid=state["oid"]
        r=store.order(oid)
        if r and 0<len(raw)<=600 and (r["user_id"]==uid or design_studio.staff(uid)):
            if uid==r["user_id"]: store.ticket(oid,uid,raw,"payment")
            else:
                if not store.get_ticket(oid): store.ticket(oid,r["user_id"],"","payment")
                store.reply_ticket(oid,uid,raw)
            user_states.pop(uid,None)
            bot.send_message(ADMIN_ID if uid==r["user_id"] else r["user_id"],"💬 #"+str(oid)+"\n"+escape(raw),reply_markup=shop_ui.menu(shop_ui.button("💬",f"x|ticket|{oid}")))
        return
    if state["mode"]=="coupon" or state.get("awaiting")=="coupon":
        if select_coupon(uid,raw,lang):
            if state["mode"]=="checkout": shop_ui.review(bot,uid,None,uid,lang,state)
            else: user_states.pop(uid,None)
        return
    if not raw: return
    if state["mode"] == "ticket":
        row = store.order(state["oid"])
        if row and row["user_id"] == uid and 0 < len(raw) <= 600:
            store.ticket(row["id"], uid, raw, "cv" if row["product_id"] == "cv" else "payment")
            user_states.pop(uid, None)
            bot.send_message(message.chat.id, shop_ui.tr(lang, "support_saved"))
            admin_panel.notify(bot, ADMIN_ID, f"💬 تذكرة دعم جديدة · #{row['id']}",
                               reply_markup=admin_panel.menu(admin_panel.button("فتح الطلب", f"opview|{row['id']}")))
        else:
            bot.send_message(message.chat.id, t(lang, "invalid_input"))
        return
    membership = is_subscribed(uid)
    if membership is None:
        bot.send_message(message.chat.id, shop_ui.tr(lang, "membership_error"))
        return
    if not membership:
        user_states.pop(uid, None)
        send_force_sub(message.chat.id, lang)
        return
    if state["mode"] == "topup":
        wallet_ui.collect(bot, message, lang, state, user_states)
    elif state["mode"] == "checkout":
        shop_ui.checkout_text(bot, message, lang, state)
    elif state["mode"] == "search":
        if not 1 <= len(raw) <= 80:
            bot.send_message(message.chat.id, t(lang, "invalid_input"))
            return
        state["query"] = raw
        products = shop_ui.search_results(lang, raw)
        if not products:
            bot.send_message(message.chat.id, shop_ui.tr(lang, "missing"))
            return
        sent = bot.send_message(message.chat.id, shop_ui.tr(lang, "search"))
        show_products(sent, lang, products, "searchpage", 0, parent="search", title=escape(raw))


def sync_order(row, lang):
    order_id = row["jrk_order_id"]
    if not order_id:
        return row
    _, data = api.jrk_request("GET", f"/orders/{order_id}", lang=lang)
    error = data.get("error", {})
    order = data.get("order") or error.get("details", {}).get("order")
    if order and order.get("id") and order.get("status"):
        items = (order.get("delivery") or {}).get("items") or []
        fraction = Decimal("1")
        if order["status"] == "completed" and "delivered_quantity" in order:
            quantity = order.get("quantity", 1)
            delivered = order["delivered_quantity"]
            if isinstance(quantity, int) and isinstance(delivered, int) and 0 <= delivered < quantity:
                fraction = Decimal(delivered) / quantity
        return db.apply_order_result(row["id"], order["id"], order["status"], "\n".join(items), fraction)
    return row


def send_delivery(chat_id, delivery):
    for offset in range(0, len(delivery), 2000):
        bot.send_message(chat_id, f"🔑 <code>{escape(delivery[offset:offset+2000])}</code>")


def run():
    if not BOT_TOKEN or not JRK_API_KEY or FORCE_SUB_CHANNEL == "@اسم_قناتك":
        raise SystemExit("Configure BOT_TOKEN, JRK_API_KEY and FORCE_SUB_CHANNEL in the environment or config.py.")
    urllib3_connection.allowed_gai_family = lambda: socket.AF_INET
    db.init_db()
    store.init()
    import logging
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stopped = checkout.worker(bot)
    print("Techo Pay Bot is running...")
    try:
        bot.infinity_polling(timeout=10, long_polling_timeout=5)
    finally:
        stopped.set()


if __name__ == "__main__":
    run()
