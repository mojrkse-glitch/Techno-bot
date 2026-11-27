import time
from decimal import Decimal as D, ROUND_CEILING
from database import transaction, change_balance
from commerce_db import audit


class PriceChanged(ValueError):
    pass


def tier(spent,cfg):
    for label,limit,pct in [("🥇",cfg.get("gold_threshold","100"),cfg.get("gold_pct","5")),
                            ("🥈",cfg.get("silver_threshold","25"),cfg.get("silver_pct","2"))]:
        if spent>=D(limit): return label,D(pct)
    return "🥉",D(0)


def can_use(c, code, uid, pid, cat, total):
    row=c.execute("SELECT * FROM coupons WHERE code=?",(code,)).fetchone()
    if not row or row["expires"] and row["expires"]<=time.time() or total<D(row["minimum"]): return None
    if row["scope"] and row["scope"] not in (pid,cat): return None
    count,mine=c.execute("""SELECT count(*),coalesce(sum(r.uid=?),0) FROM coupon_redemptions r
        JOIN checkout_orders o ON o.id=r.oid WHERE r.code=? AND
        (o.paid IS NOT NULL OR o.approval IS NOT NULL OR (o.state='invoice' AND o.expires>=unixepoch()))""",(uid,code)).fetchone()
    if count>=row["max_uses"] or mine>=row["per_user"]: return None
    return row


def quote(c, uid, pid, cat, base, stars, cost):
    cfg=dict(c.execute("SELECT key,value FROM checkout_settings"))
    spent=c.execute("SELECT total_spent FROM users WHERE user_id=?",(uid,)).fetchone()
    level,pct=tier(D(spent[0]) if spent else D(0),cfg)
    amount=base*(1-pct/100)
    selected=c.execute("SELECT code FROM selected_coupon WHERE uid=?",(uid,)).fetchone()
    coupon=can_use(c,selected[0],uid,pid,cat,base) if selected else None
    discount=D(0)
    if coupon:
        discount=D(coupon["value"])
        if coupon["kind"]=="percent": discount=amount*discount/100
        if coupon["cap"]: discount=min(discount,D(coupon["cap"]))
    floor=max(D("0.01"),cost or D(0)) if cfg.get("allow_loss")!="1" else D("0.01")
    sale=max(floor,amount-discount).quantize(D("0.01"),rounding=ROUND_CEILING)
    adjusted=stars
    if stars and sale<base:
        rate=D(cfg.get("net_usd_per_star","0"))
        if rate>0:
            adjusted=max(1,int((stars*sale/base).to_integral_value(rounding=ROUND_CEILING)))
            if cost and cfg.get("allow_loss")!="1":
                adjusted=max(adjusted,int((cost/rate).to_integral_value(rounding=ROUND_CEILING)))
    return sale,adjusted,coupon["code"] if coupon and sale<max(floor,amount) else None


def prices(uid,pid,cat,base,stars,cost):
    with transaction() as c: return quote(c,uid,pid,cat,base,stars,cost)


def reserve(c, oid, uid, pid, cost, promo):
    if not promo: return
    base,stars,cat,sale,adjusted,code,method=promo
    current=quote(c,uid,pid,cat,base,stars,cost)
    if current!=(sale,adjusted,code): raise PriceChanged()
    if code and (method=="wallet" or adjusted!=stars):
        c.execute("INSERT INTO coupon_redemptions VALUES(?,?,?)",(oid,uid,code))
    conversion=dict(c.execute("SELECT key,value FROM checkout_settings")).get("net_usd_per_star")
    sale_usd=sale if method=="wallet" else D(conversion)*adjusted if conversion and adjusted else None
    c.execute("UPDATE checkout_orders SET discount_base_stars=?, sale_usd=? WHERE id=?",
              (stars if method=="stars" else None,str(sale_usd) if sale_usd is not None else None,oid))


def delivered(c,row):
    cfg=dict(c.execute("SELECT key,value FROM checkout_settings"))
    usd=row["sale_usd"] or row["wallet_amount"]
    if usd is None and cfg.get("net_usd_per_star"):
        usd=str(D(cfg["net_usd_per_star"])*row["stars"])
    if not row["paid"]: return
    uid=row["user_id"]
    if usd is not None:
        change_balance(c,uid,D(0),D(usd))
        c.execute("INSERT OR IGNORE INTO loyalty_earnings(oid,uid,amount) VALUES(?,?,?)",(row["id"],uid,str(usd)))
    ref=c.execute("SELECT ref_by FROM users WHERE user_id=?",(uid,)).fetchone()
    if not ref or not ref[0] or ref[0]==uid: return
    if c.execute("SELECT 1 FROM checkout_orders WHERE user_id=? AND id!=? AND delivered IS NOT NULL LIMIT 1",(uid,row["id"])).fetchone(): return
    if c.execute("SELECT 1 FROM orders WHERE user_id=? AND status='completed' LIMIT 1",(uid,)).fetchone(): return
    reward=(D(cfg.get("ref_fixed","0")) or D(usd or 0)*D(cfg.get("ref_pct","0"))/100).quantize(D(".01"))
    if reward<=0: return
    if c.execute("INSERT OR IGNORE INTO referral_rewards VALUES(?,?,?,?)",(uid,ref[0],row["id"],str(reward))).rowcount:
        change_balance(c,ref[0],reward)
        audit(c,"referral_reward",ref[0],row["id"],new=str(reward))
        c.execute("INSERT OR IGNORE INTO push_queue(uid,kind,ref,unique_key) VALUES(?,'reward',?,?)",(ref[0],str(reward),"reward:"+str(uid)))
