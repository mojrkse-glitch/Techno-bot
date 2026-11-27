import time
import logging
from decimal import Decimal
from html import escape
from telebot import types
from telebot.apihelper import ApiTelegramException
import api
import suppliers
import commerce_db as store
import cv_intake
import database as db
from config import ADMIN_ID
import config
from platform_db import rows, write
from shop_ui import menu, button, label

LOG=logging.getLogger(__name__)

def order_keys(row,lang):
    oid=row["id"]
    keys=menu(button("📦 "+("عرض الطلب" if lang=="ar" else "View order"),f"star_order|{oid}"),
              button("💬",f"order_help|{oid}"))
    if row["state"]=="delivered":
        keys.row(*(button(str(n)+"⭐",f"x|rate|{oid}|{n}") for n in range(1,6)))
        for line in (row["delivery"] or "").splitlines()[:12]:
            if 0<len(line)<=256:
                keys.add(types.InlineKeyboardButton("📋 "+("نسخ" if lang=="ar" else "Copy"),copy_text=types.CopyTextButton(line)))
    return keys


def push(bot):
    for entry in rows("SELECT * FROM push_queue WHERE done=0 AND due<=unixepoch() ORDER BY id LIMIT 8"):
        uid,kind,ref=entry["uid"],entry["kind"],entry["ref"]
        if kind == "cv_paid" and not write("""UPDATE push_queue SET due=unixepoch()+120
                WHERE id=? AND done=0 AND due<=unixepoch()""", (entry["id"],)):
            continue
        try:
            lang=db.get_user(uid)["lang"]
            if kind=="order":
                row=store.order(int(ref))
                bot.send_message(uid,f"📦 #{ref} · {label(lang,row['state'])}\n{escape((row['delivery'] or '')[:2500])}",reply_markup=order_keys(row,lang))
            elif kind == "cv_paid":
                cv_intake.offer(bot, uid, uid, int(ref), lang, just_paid=True)
            elif kind=="design":
                bot.send_message(ADMIN_ID,f"🎨 طلب تصميم #{ref}",reply_markup=menu(button("فتح الطلب",f"ds|card|{ref}")))
            elif kind=="staff":
                bot.send_message(ADMIN_ID,f"⚠️ طلب يحتاج متابعة #{ref}",reply_markup=menu(button("فتح الطلب",f"adm|opview|{ref}")))
            elif kind=="reward":
                bot.send_message(uid,f"🎁 +{escape(ref)} USD · "+("مكافأة إحالة" if lang=="ar" else "Referral reward"))
            else:
                route="ds|new|"+ref[7:] if ref.startswith("design:") else "prod|"+ref
                bot.send_message(uid,"🔔 الخدمة متوفرة الآن." if lang=="ar" else "🔔 Available now.",reply_markup=menu(button("فتح / Open",route)))
            write("UPDATE push_queue SET done=1 WHERE id=?",(entry["id"],))
        except (ApiTelegramException,OSError) as exc:
            delay=60
            if isinstance(exc,ApiTelegramException):
                if exc.error_code in (403,400): delay=86400
                elif exc.error_code==429: delay=exc.result_json.get("parameters",{}).get("retry_after",60)
            write("UPDATE push_queue SET due=unixepoch()+? WHERE id=?",(delay,entry["id"]))


def check_stock():
    watches=rows("SELECT DISTINCT pid FROM stock_alerts")
    if not watches: return
    from design_studio import active
    available={p["id"] for p in api.get_products("en")} if any(not r["pid"].startswith("design:") for r in watches) else set()
    with db.transaction(write=True) as c:
        for r in watches:
            pid=r["pid"]
            if pid not in available and not (pid.startswith("design:") and active(pid[7:])): continue
            c.execute("INSERT INTO push_queue(uid,kind,ref) SELECT uid,'stock',pid FROM stock_alerts WHERE pid=?",(pid,))
            c.execute("DELETE FROM stock_alerts WHERE pid=?",(pid,))


def report():
    since=int(time.time())-86400
    with db.transaction() as c:
        def count(sql): return c.execute(sql,(since,)).fetchone()[0]
        new=count("SELECT count(*) FROM users WHERE joined_at>=?")
        creative=count("SELECT count(*) FROM design_requests WHERE created_at>=?")
        orders=count("SELECT count(*) FROM checkout_orders WHERE delivered>=?")
        pending=c.execute("SELECT count(*) FROM wallet_topups WHERE state='pending'").fetchone()[0]
        paid=list(c.execute("SELECT * FROM checkout_orders WHERE paid>=? AND refunded IS NULL",(since,)))
    rate=store.settings().get("net_usd_per_star")
    gross=sum(Decimal(r["wallet_amount"] or 0) for r in paid)
    stars=sum(r["stars"] for r in paid)
    profit=sum((Decimal(r["wallet_amount"]) if r["wallet_amount"] else Decimal(rate or 0)*r["stars"])-Decimal(r["actual_usd"]) for r in paid if r["actual_usd"] is not None and (r["wallet_amount"] or rate))
    bal=api.fetch_reseller_wallet().get("balance","—")
    second=suppliers.partner_balance()
    extra=f" · رصيد المورد الثاني {second}" if second is not None else ""
    return f"📊 آخر 24 ساعة\nمستخدمون جدد {new} · تسليمات {orders} · تصاميم {creative}\nمبيعات {stars} ⭐ و {gross:.2f} USD\nربح تقديري للطلبات ذات تكلفة وتسوية معروفتين {profit:.2f} USD\nشحن معلّق {pending} · رصيد JRK {bal}{extra}"


def maintenance(bot):
    cfg=store.settings()
    now=int(time.time())
    if now-int(cfg.get("next_stock",0))>=0:
        check_stock()
        write("INSERT OR REPLACE INTO checkout_settings VALUES('next_stock',?)",(str(now+300),))
    if now>=int(cfg.get("next_health",0)):
        balance=api.fetch_reseller_wallet().get("balance")
        if balance is not None and Decimal(str(balance))<Decimal(str(getattr(config,"LOW_BALANCE_THRESHOLD",10))):
            bot.send_message(ADMIN_ID,f"⚠️ رصيد JRK منخفض · {balance} USD")
        suppliers.alert_if_low(bot,ADMIN_ID,getattr(config,"LOW_BALANCE_THRESHOLD",10))
        write("INSERT OR REPLACE INTO checkout_settings VALUES('next_health',?)",(str(now+21600),))
    if now>=int(cfg.get("next_report",0)):
        bot.send_message(ADMIN_ID,report())
        write("DELETE FROM user_sessions WHERE expires_at<unixepoch()")
        write("INSERT OR REPLACE INTO checkout_settings VALUES('next_report',?)",(str(now+86400),))
