import json
import re
import time
from decimal import Decimal
from html import escape
from telebot import types
import api
import checkout
import commerce_db as store
import database as db
import shop_ui
from shop_ui import button,menu,render
from platform_db import rows,write
from promotions import tier
from locales import t

FAQ=[
    ("💳 كيف أشحن؟","How do I top up?","افتح المحفظة واختر الوسيلة والمبلغ. بعد التحويل أرسل مرجع الإيصال وتابع الحالة في طلبات الشحن.","Choose a method and amount in Wallet. Send the receipt reference and track it in Top-up requests."),
    ("📦 متى أستلم؟","When will I receive my order?","تعتمد المدة على الباقة. تظهر آخر حالة والتسليم في طلباتي.","Delivery time depends on your plan. Open My orders for status and delivery."),
    ("🛡 ما الضمان؟","What is covered?","راجع شروط الباقة قبل الدفع. لمراجعة مشكلة افتح تذكرة من الطلب.","Check the plan terms before paying. Open an order ticket if you need help."),
    ("🔑 كيف أفعّل الاشتراك؟","How do I activate?","افتح تعليمات التفعيل في الطلب المستلم. لا تشارك كلمة المرور أو رمز التحقق.","Open the activation instructions in the delivered order. Keep passwords and verification codes private."),
]
DOMAINS=[("الكتابة والبحث","Writing","gpt|claude|gemini|perplexity|بحث|كتابة"),("البرمجة","Coding","copilot|cursor|claude|gpt|برمجة"),("التصميم والمونتاج","Design","design"),("الألعاب والترفيه","Entertainment","games|streaming"),("مفاتيح API","API keys","api|مفتاح")]
BUDGET=[("أقل من $5","Under $5"),("$5–15","$5–15"),("أكثر من $15","Over $15")]
DURATION=[("شهري","Monthly","month|شهر"),("سنوي","Annual","annual|year|سنوي|سنة"),("شحن مباشر","Top-up","top.?up|recharge|شحن|credits")]

def loyalty(user):
    cfg=store.settings()
    spent=user["total_spent"]
    level,pct=tier(spent,cfg)
    target=Decimal(cfg.get("silver_threshold","25") if level=="🥉" else cfg.get("gold_threshold","100"))
    n=min(8,int(spent*8/target)) if target>0 else 8
    return f"{level} VIP · {pct}%  {'▰'*n}{'▱'*(8-n)}"+(f" · ↑ {max(0,target-spent):.2f} USD" if level!="🥇" else "")

def is_offer(item):
    meta=item.get("shop_meta",{})
    if meta.get("featured") or meta.get("flash_until",0)>time.time(): return True
    old=checkout.money(meta.get("compare_at_usd"))
    current=api.calc_price(item["price"],item["id"]) if old else None
    return bool(current and old>current)


def card(item,uid,lang,keys):
    pid=item["id"]
    meta=item.get("shop_meta",{})
    ar=lang=="ar"
    fav=rows("SELECT 1 FROM favorites WHERE uid=? AND pid=?",(uid,pid))
    keys.add(button(("⭐ إزالة" if fav else "☆ المفضلة") if ar else ("⭐ Remove" if fav else "☆ Save"),f"x|fav|{pid}"))
    if item.get("availability")=="unavailable": keys.add(button("🔔 أبلغني / Notify",f"x|watch|{pid}"))
    sales=rows("SELECT count(*) AS n FROM checkout_orders WHERE product_id=? AND state='delivered'",(pid,))[0]["n"]
    ratings=rows("SELECT avg(stars) AS avg,count(*) AS n FROM product_reviews WHERE pid=?",(pid,))[0]
    text=escape(str(meta.get("badge") or ""))
    if ratings["n"]: text+=f"\n⭐ {ratings['avg']:.1f} ({ratings['n']})"
    if sales: text+=f" · 📦 {sales}"
    cost=checkout.money(item.get("price"))
    price=api.calc_price(cost,pid) if cost is not None else None
    old=checkout.money(meta.get("compare_at_usd"))
    if old and price and old>price: text+=f"\n<s>${old:.2f}</s> → <b>${price:.2f}</b> (−{(1-price/old)*100:.0f}%)"
    if meta.get("flash_until",0)>time.time(): text+=f"\n⏳ {int((meta['flash_until']-time.time())/60)+1} min"
    return text

def callback(bot,uid,msg,data,lang,states):
    p=data.split("|")[1:]
    act=p[0]
    ar=lang=="ar"
    back=button("🏠","main")
    def show(text,*keys): render(bot,uid,msg,text,menu(*keys,back))
    if act=="wizard":
        opts=[DOMAINS,BUDGET,DURATION]
        selected=p[1:]
        if any(not x.isdigit() or int(x)>=len(opts[i]) for i,x in enumerate(selected[:3])): return
        if len(selected)<3:
            title=[("ما مجال الاستخدام؟","Choose your use case"),("ما ميزانيتك؟","Choose a budget"),("ما نوع الاشتراك؟","Choose a plan duration")][len(selected)][not ar]
            show(title,*[button(x[not ar],data+"|"+str(i)) for i,x in enumerate(opts[len(selected)])])
        else:
            domain,budget,duration=map(int,selected)
            found=[]
            for item in api.get_products(lang):
                price=api.calc_price(item["price"],item["id"])
                if price is None or (0 if price<5 else 1 if price<=15 else 2)!=budget: continue
                description=(item["name"]+" "+(item.get("description") or "")).lower()
                if re.search(DOMAINS[domain][2],description+" "+item["smart_cat"]) and re.search(DURATION[duration][2],description):
                    found.append(button(item["name"],"prod|"+item["id"]))
            show(("🤖 الباقات المطابقة" if found else "لا توجد باقات مطابقة حالياً.") if ar else ("Matching plans" if found else "No matching plans."),*found[:5],button("🔄","x|wizard"))
    elif act in ("offers","favorites","sort","stock"):
        items=api.get_products(lang)
        page=int(p[-1]) if p[-1].isdigit() else 0
        if act=="stock":
            items=[i for i in api.get_products(lang,include_unavailable=True) if i.get("availability")=="unavailable" and not i.get("shop_meta",{}).get("hidden")]
        elif act=="favorites":
            ids={r["pid"] for r in rows("SELECT pid FROM favorites WHERE uid=?",(uid,))}
            items=[i for i in items if i["id"] in ids]
        elif act=="offers":
            ids={r["product_id"] for r in rows("SELECT product_id FROM checkout_orders WHERE state='delivered' GROUP BY product_id ORDER BY count(*) DESC LIMIT 5")}
            items=[i for i in items if i["id"] in ids or is_offer(i)]
        elif p[1]=="price": items.sort(key=lambda i:api.calc_price(i["price"],i["id"]) or Decimal("Infinity"))
        else:
            sales={r["product_id"]:r["n"] for r in rows("SELECT product_id,count(*) n FROM checkout_orders WHERE state='delivered' GROUP BY product_id")}
            items.sort(key=lambda i:-sales.get(i["id"],0))
        prefix="x|"+act+("|"+p[1] if act=="sort" else "")
        keys=[button(i["name"],"prod|"+i["id"]) for i in items[page*8:page*8+8]]
        if page: keys.append(button("◀",prefix+"|"+str(page-1)))
        if len(items)>(page+1)*8: keys.append(button("▶",prefix+"|"+str(page+1)))
        show(("🛍 المنتجات" if items else "لا توجد باقات هنا حالياً.") if ar else ("Products" if items else "No plans here yet."),*keys)
    elif act in ("fav","watch"):
        pid=p[1]
        if not any(i["id"]==pid and not i.get("shop_meta",{}).get("hidden") for i in api.get_products(lang,include_unavailable=True)): return
        if act=="watch": write("INSERT OR IGNORE INTO stock_alerts VALUES(?,?)",(uid,pid))
        else:
            with db.transaction(write=True) as c:
                if not c.execute("DELETE FROM favorites WHERE uid=? AND pid=?",(uid,pid)).rowcount:
                    c.execute("INSERT INTO favorites VALUES(?,?)",(uid,pid))
        show("✅",button("⬅️","prod|"+pid))
    elif act=="faq":
        if len(p)>1 and p[1].isdigit() and int(p[1])<len(FAQ): show(FAQ[int(p[1])][2 if ar else 3],button("⬅️","x|faq"))
        else: show("❓ FAQ",*[button(x[not ar],f"x|faq|{i}") for i,x in enumerate(FAQ)],types.InlineKeyboardButton(t(lang,"btn_support"),url=shop_ui.support_url()))
    elif act=="rate":
        oid,n=map(int,p[1:])
        order=store.order(oid)
        if order and order["state"]=="delivered" and order["user_id"]==uid and 1<=n<=5:
            write("INSERT INTO product_reviews(oid,uid,pid,stars) VALUES(?,?,?,?) ON CONFLICT(oid) DO UPDATE SET stars=excluded.stars",(oid,uid,order["product_id"],n))
            states[uid]={"mode":"review_comment","oid":oid}
            show("⭐ شكراً! يمكنك إرسال تعليق قصير." if ar else "⭐ Thanks! Send an optional comment.")
    elif act=="coupon":
        state=states.get(uid)
        if state and state.get("mode")=="checkout": state["awaiting"]="coupon"
        else: states[uid]={"mode":"coupon"}
        show("🎟 أدخل رمز الكوبون." if ar else "🎟 Enter your coupon code.")
    elif act=="reorder":
        row=store.order(int(p[1]))
        if not row or row["user_id"]!=uid or row["state"]!="delivered" or row["product_id"]=="cv": return
        item=shop_ui.product(row["product_id"],lang)
        if not item: return show(t(lang,"unavail"))
        state={"mode":"checkout","pid":item["id"],"qty":row["quantity"],"input":item.get("input") or {},"name":item["name"],"target":json.loads(row["body"] or "{}").get("activation_identifier")}
        states[uid]=state
        shop_ui.review(bot,uid,msg,uid,lang,state)
    elif act=="ticket":
        oid=int(p[1])
        r=store.order(oid)
        from design_studio import staff
        if not r or uid!=r["user_id"] and not staff(uid): return
        if len(p)>2 and p[2]=="close":
            with db.transaction(write=True) as c:
                if c.execute("UPDATE support_tickets SET status='closed' WHERE order_id=? AND status!='closed'",(oid,)).rowcount:
                    store.audit(c,"ticket_closed",uid,oid)
        history=rows("SELECT * FROM ticket_messages WHERE oid=? ORDER BY id DESC LIMIT 8",(oid,))
        keys=[button("💬",f"x|reply|{oid}"),button("✅ إغلاق / Close",f"x|ticket|{oid}|close")]
        ticket=store.get_ticket(oid)
        show(f"📦 #{oid} · {escape(r['name'][:100])} · {shop_ui.label(lang,r['state'])}\n👤 {r['user_id']}\n💬 {ticket['status'] if ticket else '—'}\n"+"\n".join(escape(r["body"][:280]) for r in reversed(history)),*keys)
    elif act=="reply":
        r=store.order(int(p[1]))
        from design_studio import staff
        if r and (r["user_id"]==uid or staff(uid)):
            states[uid]={"mode":"thread_reply","oid":r["id"]}
            show("💬 أرسل رسالتك / Send your message")
