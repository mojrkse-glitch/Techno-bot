import time
from html import escape
from telebot.apihelper import ApiTelegramException
from telebot import types
from database import transaction
from platform_db import rows, write
from threading import Thread

def create(uid,body,photo,link,segment,confirmation=None):
    filters={"all":"1","buyers":"EXISTS(SELECT 1 FROM checkout_orders o WHERE o.user_id=users.user_id AND o.paid IS NOT NULL)",
             "balance":"CAST(balance AS REAL)>0"}
    args=()
    if segment in ("ar","en","vi","hi","fr"):
        sql="lang=?"
        args=(segment,)
    else: sql=filters[segment]
    with transaction(write=True) as c:
        existing=c.execute("SELECT id FROM campaigns WHERE confirmation=?",(confirmation,)).fetchone()
        if existing: return existing[0]
        campaign=c.execute("INSERT INTO campaigns(body,photo,link,creator,confirmation) VALUES(?,?,?,?,?)",(body,photo,link,uid,confirmation)).lastrowid
        c.execute(f"INSERT INTO campaign_targets(campaign,uid) SELECT ?,user_id FROM users WHERE is_banned=0 AND {sql}",(campaign,*args))
    return campaign

def start(bot,stop):
    def run():
        while not stop.is_set():
            batch=rows("""SELECT c.*,t.uid FROM campaign_targets t JOIN campaigns c ON c.id=t.campaign
                         WHERE t.status='pending' ORDER BY c.id,t.uid LIMIT 20""")
            if not batch:
                stop.wait(3)
                continue
            for r in batch:
                keyboard=None
                if r["link"]:
                    keyboard=types.InlineKeyboardMarkup()
                    keyboard.add(types.InlineKeyboardButton("فتح / Open",url=r["link"]))
                try:
                    if r["photo"]: bot.send_photo(r["uid"],r["photo"],caption=escape(r["body"]),reply_markup=keyboard)
                    else: bot.send_message(r["uid"],escape(r["body"]),reply_markup=keyboard)
                    status="sent"
                except ApiTelegramException as exc:
                    if exc.error_code==429:
                        stop.wait(exc.result_json.get("parameters",{}).get("retry_after",5))
                        break
                    status="blocked" if exc.error_code==403 else "failed"
                except OSError: status="failed"
                write("UPDATE campaign_targets SET status=? WHERE campaign=? AND uid=?",(status,r["id"],r["uid"]))
                if not rows("SELECT 1 FROM campaign_targets WHERE campaign=? AND status='pending' LIMIT 1",(r["id"],)):
                    stats=rows("SELECT status,count(*) AS n FROM campaign_targets WHERE campaign=? GROUP BY status",(r["id"],))
                    try: bot.send_message(r["creator"],"📣 #"+str(r["id"])+" · "+", ".join(f"{v['status']} {v['n']}" for v in stats))
                    except (ApiTelegramException,OSError): pass
                if stop.wait(.06): return
    Thread(target=run,daemon=True,name="broadcast").start()
