import csv
import io
import re
import sqlite3
import tempfile
from pathlib import Path
from decimal import Decimal
from html import escape
from urllib.parse import urlsplit
import time
import secrets
from app_settings import ADMIN_ID
import database as db
import commerce_db as store
import design_studio as design
import wallet
import broadcast
from platform_db import rows,write
from shop_ui import button,menu,render
from notifications import report

PROMPTS={
    "coupon":"أدخل الرمز | percent أو fixed_usd | القيمة | الحد الأدنى | سقف الخصم (0 بلا سقف) | الصلاحية بالأيام | الاستخدامات الإجمالية | لكل عميل | فئة أو معرّف المنتج أو *",
    "rule":"أدخل اسم الإعداد ثم القيمة. الخيارات: silver_threshold, gold_threshold, silver_pct, gold_pct, ref_pct, ref_fixed, allow_loss, avatar_limit, avatar_period (month/lifetime).",
    "team":"أدخل معرّف الموظف ثم support أو remove.",
    "campaign":"اكتب الجمهور ثم | ثم نص الرسالة، وأضف اختيارياً | ثم رابط https. الجمهور all أو buyers أو balance أو رمز اللغة. يمكنك إرفاق صورة وإدخال التفاصيل في الوصف.",
    "destination":"أرسل رقم أو عنوان الاستلام الجديد.",
    "brief":"أرسل نص التعليمات الجديد.",
    "metadata":"أدخل معرّف المنتج | الحقل | القيمة. الحقول badge, compare_at_usd, featured (0/1), flash (ساعات|سعر), photo (أرفق صورة واكتب في الوصف معرّف المنتج|photo).",
    "adjust":"أدخل مبلغ التعديل بإشارته ثم مسافة وسبب التعديل.",
    "dm":"اكتب الرسالة المراد إرسالها إلى العميل.",
}

def callback(bot,uid,msg,data,states):
    if not design.staff(uid): return
    p=data.split("|")[1:]
    act=p[0] if p else "home"
    arg=p[1] if len(p)>1 else ""
    def show(text,*keys): render(bot,uid,msg,text,menu(*keys,button("⬅️ إدارة المتجر","op|home")))
    if act=="home":
        states.pop(uid,None)
        keys=[button("🎨 طلبات صمّملي","ds|queue"),button("💬 تذاكر الدعم","op|tickets"),button("📦 متابعة الطلبات","op|orders")]
        if uid==ADMIN_ID:
            keys.extend(button(label,"op|"+action) for label,action in [
                ("🎨 إعدادات الخدمات","design"),("💳 وسائل الشحن","methods"),("🎟 إنشاء كوبون","coupon"),("🎁 الولاء والإحالات","rule"),
                ("📣 حملة","campaign"),("👥 فريق الدعم","team"),("🏷 عروض وبطاقات","metadata"),("📊 ملخص يومي","report"),
                ("📥 تصدير CSV","export"),("💾 نسخة SQLite","backup")])
            keys.append(button("لوحة الإدارة","adm|home"))
        render(bot,uid,msg,"🛡 إدارة المتجر",menu(*keys,button("🏠","main")))
        return
    if act in ("orders","tickets"):
        page=int(arg or 0)
        items=rows("SELECT id AS oid,state AS label FROM checkout_orders ORDER BY id DESC LIMIT 9 OFFSET ?" if act=="orders" else "SELECT order_id AS oid,status AS label FROM support_tickets ORDER BY updated DESC LIMIT 9 OFFSET ?",(page*8,))
        keys=[button(f"#{r['oid']} · {r['label']}",f"x|ticket|{r['oid']}") for r in items[:8]]
        if page: keys.append(button("◀",f"op|{act}|{page-1}"))
        if len(items)>8: keys.append(button("▶",f"op|{act}|{page+1}"))
        show("📦 سجل المتابعة",*keys)
        return
    if uid!=ADMIN_ID: return
    cfg=store.settings()
    if act=="design":
        keys=[]
        for k,v in design.SERVICES.items():
            keys.append(button(("🟢 " if design.active(k) else "⏸ ")+v[0],"op|toggle_design|"+k))
            if k != "cv":
                keys.append(button("✏️ "+v[0],"op|brief|"+k))
        show("🎨 اضغط على الخدمة لتغيير حالتها، أو على القلم لتعديل التعليمات.",*keys)
    elif act=="methods":
        keys=[]
        for k,(name,_,dest,*_) in wallet.METHODS.items():
            keys.extend([button(("🟢 " if cfg.get("enable:"+k,"1")=="1" else "⏸ ")+name,"op|toggle_method|"+k),
                         button("✏️ "+cfg.get("dest:"+k,dest),"op|destination|"+k)])
        show("💳 وسائل الشحن",*keys)
    elif act=="toggle_design" and arg in design.SERVICES:
        store.set_setting("design_on:"+arg,int(not design.active(arg)),uid)
        callback(bot,uid,msg,"op|design",states)
    elif act=="toggle_method" and arg in wallet.METHODS:
        store.set_setting("enable:"+arg,int(cfg.get("enable:"+arg,"1")!="1"),uid)
        callback(bot,uid,msg,"op|methods",states)
    elif act=="ban":
        if int(arg)==ADMIN_ID: return
        if len(p)<3:
            current=rows("SELECT is_banned FROM users WHERE user_id=?",(int(arg),))
            if not current: return
            target=1-int(bool(current[0]["is_banned"]))
            question="تأكيد حظر الحساب؟" if target else "تأكيد فكّ حظر الحساب؟"
            show(question,button("تأكيد",data+"|"+str(target)))
            return
        if p[2] not in ("0","1"): return
        target=int(p[2])
        with db.transaction(write=True) as c:
            if c.execute("UPDATE users SET is_banned=? WHERE user_id=? AND is_banned!=?",(target,int(arg),target)).rowcount:
                store.audit(c,"ban_status",uid,old=str(1-target),new=str(target),detail=arg)
        show("تم تحديث حالة الحساب.")
    elif act=="report": show(report())
    elif act=="export":
        show("📥 اختر نوع التقرير والفترة",*[button(f"{name} · {d} يوم",f"op|csv|{key}|{d}") for key,name in (("orders","طلبات"),("topups","شحن")) for d in (7,30,90)])
    elif act=="csv" and arg in ("orders","topups") and p[2] in ("7","30","90"):
        sql="SELECT id,user_id,product_id,state,stars,wallet_amount,actual_usd,created FROM checkout_orders WHERE created>=?" if arg=="orders" else "SELECT id,user_id,method,state,amount,currency,credit,submitted FROM wallet_topups WHERE submitted>=?"
        out=io.StringIO()
        writer=csv.writer(out)
        with db.transaction() as c:
            cur=c.execute(sql,(int(time.time())-86400*int(p[2]),))
            writer.writerow([i[0] for i in cur.description])
            writer.writerows(cur)
        datafile=io.BytesIO(out.getvalue().encode("utf-8-sig"))
        datafile.name=arg+".csv"
        bot.send_document(uid,datafile)
    elif act=="backup":
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"techo-backup.sqlite"
            with db.transaction() as src:
                dst=sqlite3.connect(path)
                try: src.backup(dst)
                finally: dst.close()
            with path.open("rb") as f: bot.send_document(uid,f)
    elif act in PROMPTS:
        states[uid]={"mode":"operation","action":act,"arg":arg}
        show(PROMPTS[act])
    elif act=="commit":
        draft=states.get(uid,{})
        if draft.get("mode")!="operation_confirm" or draft["key"]!=arg: return
        states.pop(uid,None)
        apply(bot,uid,draft["action"],draft["arg"],draft["text"],draft["photo"],draft["key"])
        show("✅ تم التنفيذ.")


def collect(bot,m,state,states):
    if m.from_user.id!=ADMIN_ID: return
    raw=(m.text or getattr(m,"caption",None) or "").strip()
    if not raw or len(raw)>950: return
    key=secrets.token_hex(10)
    state.update(mode="operation_confirm",text=raw,photo=m.photo[-1].file_id if getattr(m,"photo",None) else "",key=key)
    bot.send_message(ADMIN_ID,"تأكيد الإجراء؟\n"+escape(raw),reply_markup=menu(button("✅ تأكيد","op|commit|"+key),button("إلغاء","op|home")))

def num(raw,minimum=0,maximum=10**9):
    x=Decimal(raw)
    if not x.is_finite() or not minimum<=x<=maximum or x!=x.quantize(Decimal(".01")): raise ValueError()
    return x

def apply(bot,uid,act,arg,text,photo,key):
    if uid!=ADMIN_ID: return
    fields=[v.strip() for v in text.split("|")]
    if act=="coupon":
        code,kind,value,minimum,cap,days,total,per,scope=fields
        code=code.upper()
        if not re.fullmatch("[A-Z0-9_-]{1,32}",code) or kind not in ("percent","fixed_usd"): raise ValueError()
        value=num(value,Decimal(".01"),100 if kind=="percent" else 10**6)
        if not 1<=int(per)<=int(total)<=1000000 or not 1<=int(days)<=3650: raise ValueError()
        with db.transaction(write=True) as c:
            c.execute("INSERT INTO coupons VALUES(?,?,?,?,?,?,?,?,?)",(code,kind,str(value),str(num(minimum)),str(num(cap)) if num(cap) else None,int(time.time())+int(days)*86400,int(total),int(per),scope if scope!="*" else ""))
            store.audit(c,"coupon_create",uid,detail=code)
    elif act=="rule":
        k,v=text.split()
        if k=="avatar_period":
            if v not in ("month","lifetime"): raise ValueError()
        elif k in ("silver_pct","gold_pct","ref_pct"): num(v,0,100)
        elif k in ("silver_threshold","gold_threshold","ref_fixed"): num(v)
        elif k=="avatar_limit":
            if not 0<=int(v)<=100: raise ValueError()
        elif k=="allow_loss":
            if v not in ("0","1"): raise ValueError()
        else: raise ValueError()
        store.set_setting(k,v,uid)
    elif act=="team":
        user,role=text.split()
        if not user.isdigit() or role not in ("support","remove"): raise ValueError()
        store.set_setting("staff:"+user,role,uid)
    elif act=="destination" and arg in wallet.METHODS:
        pattern={"usdt":"0x[0-9a-fA-F]{40}","syriatel":"[0-9]{10}","sham_usd":"[A-Za-z0-9]{32}","sham_syp":"[A-Za-z0-9]{32}"}[arg]
        if not re.fullmatch(pattern,text): raise ValueError()
        store.set_setting("dest:"+arg,text,uid)
    elif act=="brief" and arg in design.SERVICES: store.set_setting("brief:"+arg,text,uid)
    elif act=="metadata":
        pid,k,*vals=fields
        value="|".join(vals)
        if k=="photo" and photo: k,value="photo_file_id",photo
        elif k=="compare_at_usd": value=str(num(value))
        elif k=="featured" and value in ("0","1"): value=value=="1"
        elif k=="flash" and len(vals)==2:
            hours,price=vals
            if not 1<=int(hours)<=8760: raise ValueError()
            store.edit_product(pid,"flash_price",str(num(price,Decimal(".01"))),uid)
            k,value="flash_until",int(time.time())+3600*int(hours)
        elif k=="badge": value=value[:60]
        else: raise ValueError()
        store.edit_product(pid,k,value,uid)
    elif act=="adjust":
        amount,reason=text.split(maxsplit=1)
        value=num(amount,-10**8,10**8)
        with db.transaction(write=True) as c:
            r=c.execute("SELECT balance FROM users WHERE user_id=?",(int(arg),)).fetchone()
            if not r or Decimal(r[0])+value<0 or value==0: raise ValueError()
            if c.execute("SELECT 1 FROM checkout_events WHERE action=? AND detail=?",("adjust:"+key,reason)).fetchone(): return
            balance=db.change_balance(c,int(arg),value)
            store.audit(c,"adjust:"+key,uid,old=r[0],new=str(balance),detail=reason)
        bot.send_message(int(arg),f"💳 {value:+} USD · {escape(reason)}")
    elif act=="dm": bot.send_message(int(arg),"💬 "+escape(text))
    elif act=="campaign":
        if len(fields) not in (2,3) or fields[0] not in ("all","buyers","balance","ar","en","hi","vi","fr"): raise ValueError()
        link=fields[2] if len(fields)==3 else ""
        if link and (urlsplit(link).scheme!="https" or not urlsplit(link).hostname): raise ValueError()
        broadcast.create(uid,fields[1],photo,link,fields[0],key)
