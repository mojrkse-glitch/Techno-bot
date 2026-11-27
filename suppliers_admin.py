"""شاشات الإدارة للموردين: الحالة، المنتجات المرتبطة، مقترحات الربط، منتجات المورد الثاني، والإعدادات.

كل الأزرار تمر عبر panel.button فتصل كـ adm|sp|... وتُوجَّه من admin_panel.handle_callback.
لا حاجة لكتابة نصوص: كل شيء بالأزرار (التبديل، الدورة بين الكميات، الاعتماد بضغطة).
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from html import escape

import api
import commerce_db as store
import partner_api
import suppliers
import ui_style

PAGE = 8
QTY_STEPS = (1, 2, 5, 10, 20, 50)


def cost_of(item):
    return Decimal(str(item["price"]))


def sell_of(item):
    return api.calc_price(cost_of(item), item["id"])


def money(value):
    return f"${value:.2f}" if value is not None else "—"


def merged():
    return api.get_products("en", include_unavailable=True)


def state_dot(on):
    return "🟢" if on else "🔴"


def pager(panel, keyboard, page, more, route):
    panel.add_pages(keyboard, page, more, route)


def styled(panel, label, action, style):
    return ui_style.styled(panel.button(label, action), style)


# ───────────────────────── الشاشة الرئيسية ─────────────────────────

def home(panel, bot, chat, msg, note=""):
    settings = store.settings()
    configured = partner_api.configured()
    enabled = suppliers.partner_enabled(settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jrk_wallet = pool.submit(api.fetch_reseller_wallet)
        gs_balance = pool.submit(suppliers.partner_balance) if enabled else None
        jrk_balance = jrk_wallet.result().get("balance")
        second_balance = gs_balance.result() if gs_balance else None
    items = suppliers.partner_items(force=True, settings=settings) if enabled else []
    jrk = api.load_catalog("en", False)
    links = suppliers.link_rows()
    pending = [row for row in suppliers.suggestions(jrk, items) if row["level"] in {"high", "medium"}] if items else []
    unavailable = sum(1 for item in items if item["availability"] == "unavailable")
    lines = ["🔌 <b>الموردون</b>", ""]
    lines.append(f"JRK AI — {state_dot(jrk_balance is not None)} "
                 + (f"الرصيد ${Decimal(str(jrk_balance)):.2f}" if jrk_balance is not None else "تعذّر الاتصال")
                 + f" · {len(jrk)} منتج")
    if not configured:
        lines.append("المورد الثاني — ⚠️ لم يُضبط PARTNER_API_KEY في متغيرات الاستضافة")
    elif not enabled:
        lines.append("المورد الثاني — ⏸ متوقف من الإعدادات")
    else:
        lines.append(f"المورد الثاني — {state_dot(second_balance is not None)} "
                     + (f"الرصيد ${Decimal(second_balance):.2f}" if second_balance is not None else "تعذّر الاتصال")
                     + f" · {len(items)} منتج ({len(items) - unavailable} متوفر)")
    lines += ["", f"🔗 منتجات مرتبطة: <b>{len(links)}</b>", f"✨ مقترحات ربط بانتظار قرارك: <b>{len(pending)}</b>"]
    if note:
        lines += ["", escape(note)]
    on_off = lambda flag: "مفعّل" if flag else "معطّل"
    keyboard = panel.menu(
        panel.button(f"🔗 المرتبطة ومقارنة الأسعار · {len(links)}", "sp|links|0"),
        panel.button(f"✨ مقترحات الربط · {len(pending)}", "sp|sugg|0"),
        panel.button("🧷 ربط يدوي لمنتج", "sp|mlist|0"),
        panel.button("📦 منتجات المورد الثاني", "sp|gs|0"),
        styled(panel, f"المورد الثاني: {on_off(enabled)}", "sp|set|gs_enabled", ui_style.SUCCESS if enabled else ui_style.DANGER),
        styled(panel, f"إظهار غير المتوفر بزر أحمر: {on_off(suppliers.show_unavailable(settings))}", "sp|set|show_unavailable",
               ui_style.SUCCESS if suppliers.show_unavailable(settings) else ui_style.DANGER),
        panel.button(f"🔢 أقصى كمية شراء للمورد الثاني: {suppliers.max_quantity(settings)}", "sp|set|gs_max_qty"),
        styled(panel, f"🤖 ربط تلقائي للمطابقات العالية: {on_off(suppliers.auto_link_enabled(settings))}", "sp|set|gs_auto_link",
               ui_style.SUCCESS if suppliers.auto_link_enabled(settings) else ui_style.DANGER),
        panel.button("🧪 فحص الاتصال", "sp|test"),
        panel.button("⬅️ اللوحة", "home"))
    panel.screen(bot, chat, msg, "\n".join(lines), keyboard)


def toggle(panel, bot, chat, msg, uid, key):
    settings = store.settings()
    if key == "gs_max_qty":
        current = suppliers.max_quantity(settings)
        value = next((n for n in QTY_STEPS if n > current), QTY_STEPS[0])
    elif key in {"gs_enabled", "show_unavailable"}:
        value = "0" if settings.get(key, "1") != "0" else "1"
    elif key == "gs_auto_link":
        value = "0" if settings.get(key, "0") == "1" else "1"
    else:
        return
    store.set_setting(key, value, uid)
    if key == "gs_enabled":
        suppliers.reset_cache()
    home(panel, bot, chat, msg, "تم حفظ الإعداد.")


def test(panel, bot, chat, msg):
    wallet = api.fetch_reseller_wallet()
    lines = ["🧪 <b>فحص الاتصال</b>", ""]
    lines.append("JRK: " + (f"✅ يعمل · الرصيد ${Decimal(str(wallet['balance'])):.2f}" if "balance" in wallet else "❌ تعذّر الاتصال"))
    if not partner_api.configured():
        lines.append("المورد الثاني: ⚠️ أضف PARTNER_API_KEY في متغيرات الاستضافة ثم أعد التشغيل.")
    else:
        alive, maintenance = partner_api.health()
        balance = partner_api.balance()
        lines.append("المورد الثاني: " + ("🛠 في وضع الصيانة" if maintenance else "✅ الخدمة تعمل" if alive else "❌ الخدمة لا تستجيب"))
        lines.append("المفتاح: " + (f"✅ صالح · الرصيد ${Decimal(balance):.2f}" if balance is not None else "❌ غير صالح أو الحساب موقوف"))
    panel.screen(bot, chat, msg, "\n".join(lines), panel.menu(panel.button("🔄 إعادة الفحص", "sp|test"), panel.button("⬅️ الموردون", "sp|home")))


# ───────────────────────── المرتبطة ومقارنة الأسعار ─────────────────────────

def links_list(panel, bot, chat, msg, page):
    products = [p for p in merged() if p.get("partner_id")]
    products.sort(key=lambda p: p["name"].casefold())
    keyboard = panel.menu()
    for item in products[page * PAGE:(page + 1) * PAGE]:
        offers = item["offers"]
        win = suppliers.SHORT if item["supplier"] == "gs" else "JRK"
        label = f"{item['name'][:24]} · {money(offers['jrk']['cost'])} | {money(offers['gs']['cost'])} ← {win}"
        keyboard.add(ui_style.styled(panel.button(label, f"sp|link|{item['partner_id']}"),
                                     ui_style.SUCCESS if item["availability"] != "unavailable" else ui_style.DANGER))
    pager(panel, keyboard, page, len(products) > (page + 1) * PAGE, "sp|links")
    keyboard.add(panel.button("⬅️ الموردون", "sp|home"))
    text = "🔗 <b>المنتجات المرتبطة</b>\nالسعر: JRK | المورد الثاني ← من سيُباع منه الآن (الأرخص المتاح)."
    if not products:
        text += "\n\nلا توجد منتجات مرتبطة بعد. افتح «مقترحات الربط»."
    panel.screen(bot, chat, msg, text, keyboard)


def offer_line(name, offer):
    stock = "—" if offer["stock"] is None else offer["stock"]
    state = "متوفر" if offer["available"] else "نفد"
    return f"{name}: <b>{money(offer['cost'])}</b> · {state} · 📦 {stock}"


def link_card(panel, bot, chat, msg, partner_id, note=""):
    item = next((p for p in merged() if p.get("partner_id") == partner_id), None)
    if not item:
        panel.screen(bot, chat, msg, "الربط غير موجود أو أحد المنتجين لم يعد في الكتالوج.", panel.menu(panel.button("⬅️ المرتبطة", "sp|links|0")))
        return
    offers = item["offers"]
    sell = sell_of(item)
    winner = "المورد الثاني" if item["supplier"] == "gs" else "JRK"
    text = (f"🔗 <b>{escape(item['name'][:100])}</b>\n\n{offer_line('JRK', offers['jrk'])}\n"
            f"{offer_line('المورد الثاني', offers['gs'])}\n\n"
            f"الأرخص المتاح الآن: <b>{winner}</b>\nسعر البيع للعميل: <b>{money(sell)}</b>"
            + (f" · الربح التقديري {money(sell - cost_of(item))}" if sell is not None else " · البيع متوقف (التكلفة أعلى من السعر المحدد)"))
    if note:
        text += f"\n\n{escape(note)}"
    panel.screen(bot, chat, msg, text, panel.menu(panel.button("💰 تسعير المنتج", f"product|{item['id']}"),
                                                    panel.button("🗑 فك الربط", f"sp|un|{partner_id}"),
                                                    panel.button("⬅️ المرتبطة", "sp|links|0")))


# ───────────────────────── المقترحات والربط ─────────────────────────

def pair_card(panel, bot, chat, msg, view, primary, partner, score=None, lvl=None):
    view["sp_pair"] = [partner["id"], primary["id"]]
    cheaper = "المورد الثاني" if cost_of(partner) < cost_of(primary) else "JRK"
    text = (f"✨ <b>هل هما نفس المنتج؟</b>\n\n<b>JRK</b>\n{escape(primary['name'][:110])}\nالتكلفة {money(cost_of(primary))}"
            f" · 📦 {suppliers.jrk_stock(primary) if suppliers.jrk_stock(primary) is not None else '—'}\n\n"
            f"<b>المورد الثاني</b>\n{escape(partner['name'][:110])}\nالتكلفة {money(cost_of(partner))} · 📦 {partner['stock_count']}"
            f" · {partner['delivery_type']}\n\nالأرخص: <b>{cheaper}</b>")
    if score is not None:
        text += f"\nدرجة التشابه: {int(score * 100)}% · " + {"high": "مطابقة عالية", "medium": "تحتاج مراجعتك"}.get(lvl, "")
    text += "\n\nتأكد أن المدة والباقة والبلد متطابقة قبل الربط."
    keyboard = panel.menu(styled(panel, "✅ نعم، اربطهما", f"sp|ok|{partner['id']}", ui_style.SUCCESS),
                          styled(panel, "🚫 ليسا نفس المنتج", f"sp|no|{partner['id']}", ui_style.DANGER),
                          panel.button("⬅️ المقترحات", "sp|sugg|0"))
    panel.screen(bot, chat, msg, text, keyboard)


def suggestion_list(panel, bot, chat, msg, view, page):
    found = suppliers.suggestions(api.load_catalog("en", False), suppliers.partner_items())
    found = [row for row in found if row["level"] in {"high", "medium"}]
    view["sp_pairs"] = {row["partner"]["id"]: row["primary"]["id"] for row in found}
    keyboard = panel.menu()
    high = [row for row in found if row["level"] == "high"]
    if high:
        keyboard.add(styled(panel, f"✅ اعتماد كل المطابقات العالية ({len(high)})", "sp|appall", ui_style.SUCCESS))
    for row in found[page * PAGE:(page + 1) * PAGE]:
        mark = "🟢" if row["level"] == "high" else "🟡"
        keyboard.add(panel.button(f"{mark} {row['primary']['name'][:26]} ⟷ {row['partner']['name'][:20]}", f"sp|sg|{row['partner']['id']}"))
    pager(panel, keyboard, page, len(found) > (page + 1) * PAGE, "sp|sugg")
    keyboard.add(panel.button("⬅️ الموردون", "sp|home"))
    text = "✨ <b>مقترحات الربط</b>\n🟢 مطابقة عالية (نفس العلامة والمدة)  🟡 تحتاج نظرك."
    if not found:
        text += "\n\nلا توجد مقترحات جديدة."
    panel.screen(bot, chat, msg, text, keyboard)


def find_pair(view, partner_id):
    primary_id = (view.get("sp_pairs") or {}).get(partner_id)
    partner = next((q for q in suppliers.partner_items() if q["id"] == partner_id), None)
    primary = next((p for p in api.load_catalog("en", False) if p["id"] == primary_id), None)
    return primary, partner


def approve_all(panel, bot, chat, msg, uid, view, confirmed):
    found = [row for row in suppliers.suggestions(api.load_catalog("en", False), suppliers.partner_items()) if row["level"] == "high"]
    if not confirmed:
        sample = "\n".join(f"• {escape(r['primary']['name'][:40])} ⟷ {escape(r['partner']['name'][:30])}" for r in found[:6])
        panel.screen(bot, chat, msg, f"سيتم ربط <b>{len(found)}</b> منتج مطابقة عالية:\n{sample}\n\nيمكنك فك أي ربط لاحقاً.",
                     panel.menu(styled(panel, "✅ تأكيد الربط", "sp|appall|go", ui_style.SUCCESS), panel.button("⬅️ المقترحات", "sp|sugg|0")))
        return
    for row in found:
        suppliers.link(row["primary"]["id"], row["partner"]["id"], uid, "auto")
    home(panel, bot, chat, msg, f"تم ربط {len(found)} منتج.")


def manual_list(panel, bot, chat, msg, view, page):
    linked = {r["primary_id"] for r in suppliers.link_rows()}
    products = sorted((p for p in api.load_catalog("en", False) if p["id"] not in linked and not suppliers.needs_target(p)),
                      key=lambda p: p["name"].casefold())
    chunk = products[page * PAGE:(page + 1) * PAGE]
    view["sp_ids"] = [p["id"] for p in chunk]
    keyboard = panel.menu(*(panel.button(f"{p['name'][:34]} · {money(cost_of(p))}", f"sp|mpick|{i}") for i, p in enumerate(chunk)))
    pager(panel, keyboard, page, len(products) > (page + 1) * PAGE, "sp|mlist")
    keyboard.add(panel.button("⬅️ الموردون", "sp|home"))
    panel.screen(bot, chat, msg, "🧷 <b>ربط يدوي</b>\nاختر منتج JRK الذي تريد ربطه بمنتج من المورد الثاني." +
                 ("" if products else "\n\nلا توجد منتجات غير مرتبطة."), keyboard)


def manual_pick(panel, bot, chat, msg, view, index):
    ids = view.get("sp_ids") or []
    primary = next((p for p in api.load_catalog("en", False) if index < len(ids) and p["id"] == ids[index]), None)
    if not primary:
        manual_list(panel, bot, chat, msg, view, 0)
        return
    view["sp_pick"] = primary["id"]
    options = suppliers.candidates_for(primary, suppliers.partner_items())
    keyboard = panel.menu(*(ui_style.availability_button(f"{q['name'][:30]} · {money(cost_of(q))} · 📦 {q['stock_count']}",
                                                                   f"adm|sp|mc|{q['id']}", q["availability"] == "available") for q in options))
    keyboard.add(panel.button("⬅️ القائمة", "sp|mlist|0"))
    panel.screen(bot, chat, msg, f"🧷 <b>{escape(primary['name'][:90])}</b>\nالتكلفة عند JRK {money(cost_of(primary))}\n\nاختر المنتج المقابل من المورد الثاني:" +
                 ("" if options else "\n\nلا توجد منتجات متاحة للربط."), keyboard)


def commit_link(panel, bot, chat, msg, uid, view, partner_id):
    pair = view.pop("sp_pair", None)
    if not pair or pair[0] != partner_id:
        home(panel, bot, chat, msg, "انتهت صلاحية هذه الشاشة. افتح المقترح من جديد.")
        return
    suppliers.link(pair[1], pair[0], uid)
    link_card(panel, bot, chat, msg, partner_id, "تم الربط. سيُعرض الأرخص المتاح للعملاء تلقائياً.")


# ───────────────────────── منتجات المورد الثاني ─────────────────────────

def partner_list(panel, bot, chat, msg, page):
    items = sorted(suppliers.partner_items(), key=lambda i: (i["availability"] != "available", i["name"].casefold()))
    keyboard = panel.menu()
    for item in items[page * PAGE:(page + 1) * PAGE]:
        sell = sell_of(item)
        label = f"{item['name'][:24]} · {money(cost_of(item))}→{money(sell)} · 📦 {item['stock_count']}"
        keyboard.add(ui_style.availability_button(label, f"adm|sp|gp|{item['id']}", item["availability"] == "available"))
    pager(panel, keyboard, page, len(items) > (page + 1) * PAGE, "sp|gs")
    keyboard.add(panel.button("🔄 تحديث من المورد", "sp|gsr"), panel.button("⬅️ الموردون", "sp|home"))
    text = "📦 <b>منتجات المورد الثاني</b>\nالتكلفة ← سعر البيع · 📦 الكمية. الأخضر متوفر والأحمر نفد."
    if not items:
        text += "\n\nلا توجد منتجات (تأكد من المفتاح والاتصال)."
    panel.screen(bot, chat, msg, text, keyboard)


def partner_card(panel, bot, chat, msg, partner_id):
    item = next((p for p in suppliers.partner_items() if p["id"] == partner_id), None)
    if not item:
        panel.screen(bot, chat, msg, "المنتج لم يعد في كتالوج المورد.", panel.menu(panel.button("⬅️ المنتجات", "sp|gs|0")))
        return
    linked = next((r["primary_id"] for r in suppliers.link_rows() if r["partner_id"] == partner_id), None)
    sell = sell_of(item)
    hidden = bool(store.edits().get(partner_id, {}).get("hidden"))
    text = (f"📦 <b>{escape(item['name'])}</b>\nالنوع: {item['delivery_type']} · الرمز: <code>{escape(str(item['partner_code'] or '—'))}</code>\n"
            f"التكلفة: <b>{money(cost_of(item))}</b> · البيع: <b>{money(sell)}</b>\n"
            f"المتوفر: <b>{item['stock_count']}</b> · الحالة: {'متوفر' if item['availability'] == 'available' else 'نفد'}\n"
            f"الربط: {'مرتبط مع منتج JRK' if linked else 'غير مرتبط'}" + ("\n👁 مخفي عن العملاء" if hidden else ""))
    keyboard = panel.menu(panel.button("💰 التسعير", f"product|{partner_id}"),
                          panel.button("👁 إظهار" if hidden else "🙈 إخفاء", f"ophide|{partner_id}|{int(not hidden)}"),
                          panel.button("⬅️ المنتجات", "sp|gs|0"))
    panel.screen(bot, chat, msg, text, keyboard)


# ───────────────────────── التوجيه ─────────────────────────

def callback(panel, bot, call, view, args):
    uid, chat, msg = call.from_user.id, call.message.chat.id, call.message.message_id
    act = args[0] if args else "home"
    rest = args[1:]
    page = int(rest[0]) if rest and rest[0].isdigit() else 0
    if act == "home":
        home(panel, bot, chat, msg)
    elif act == "set":
        toggle(panel, bot, chat, msg, uid, rest[0])
    elif act == "test":
        test(panel, bot, chat, msg)
    elif act == "links":
        links_list(panel, bot, chat, msg, page)
    elif act == "link":
        link_card(panel, bot, chat, msg, rest[0])
    elif act == "un":
        suppliers.unlink(rest[0], uid)
        links_list(panel, bot, chat, msg, 0)
    elif act == "sugg":
        suggestion_list(panel, bot, chat, msg, view, page)
    elif act in {"sg", "mc"}:
        if act == "sg":
            primary, partner = find_pair(view, rest[0])
        else:
            partner = next((q for q in suppliers.partner_items() if q["id"] == rest[0]), None)
            primary = next((p for p in api.load_catalog("en", False) if p["id"] == view.get("sp_pick")), None)
        if primary and partner:
            score = None
            lvl = None
            if act == "sg":
                pf, qf = suppliers.features(primary["name"]), suppliers.features(partner["name"], partner.get("duration_days"))
                score = suppliers.similarity(pf, qf)
                lvl = suppliers.level(score, pf, qf)
            pair_card(panel, bot, chat, msg, view, primary, partner, score, lvl)
        else:
            suggestion_list(panel, bot, chat, msg, view, 0)
    elif act == "ok":
        commit_link(panel, bot, chat, msg, uid, view, rest[0])
    elif act == "no":
        pair = view.pop("sp_pair", None)
        if pair and pair[0] == rest[0]:
            suppliers.reject(pair[1], pair[0], uid)
        suggestion_list(panel, bot, chat, msg, view, 0)
    elif act == "appall":
        approve_all(panel, bot, chat, msg, uid, view, bool(rest and rest[0] == "go"))
    elif act == "mlist":
        manual_list(panel, bot, chat, msg, view, page)
    elif act == "mpick":
        manual_pick(panel, bot, chat, msg, view, page)
    elif act == "gs":
        partner_list(panel, bot, chat, msg, page)
    elif act == "gsr":
        suppliers.partner_items(force=True)
        partner_list(panel, bot, chat, msg, 0)
    elif act == "gp":
        partner_card(panel, bot, chat, msg, rest[0])
    else:
        home(panel, bot, chat, msg)
