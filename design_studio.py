import json
import time
import secrets
from html import escape
from telebot import types
from config import ADMIN_ID
from database import transaction, get_user
from commerce_db import settings, set_setting, audit
from platform_db import rows, write
from shop_ui import begin, render, button, menu

SERVICES = {
    "cv": ("📄 سيرة ذاتية (CV)", "📄 Résumé (CV)", "", ""),
    "poster": ("🖼 بوستر احترافي", "🖼 Poster", "ما موضوع البوستر؟ أرسل النص، المقاس أو المنصة، الألوان والشعار أو الصور المرجعية.", "Send the subject, text, dimensions or platform, colors and reference images."),
    "free_avatar": ("🎁 صورة شخصية مجانية", "🎁 Free portrait", "أرسل صورة شخصية واضحة ووصفاً للأسلوب والخلفية المطلوبين.", "Send a clear portrait and describe the style and background."),
    "portfolio": ("💼 بورتفوليو احترافي", "💼 Portfolio", "أرسل تخصصك ونبذة عنك وروابط أبرز أعمالك، وحدّد الصيغة المطلوبة (موقع، PDF أو عرض).", "Send your field, biography, project links and preferred format (website, PDF or slides)."),
    "ai_video": ("🎬 فيديو AI", "🎬 AI video", "أرسل الفكرة، المدة، الأسلوب، المقاس، وهل تحتاج إلى تعليق صوتي أو موسيقى.", "Send the concept, length, visual style, aspect ratio and audio needs."),
}
STATUS = {"pending_review":"بانتظار المراجعة","in_progress":"قيد التنفيذ","completed":"تم التسليم","rejected":"مرفوض","cancelled":"ملغى"}
def active(service):
    return settings().get("design_on:"+service, "1")=="1"

def staff(uid):
    return uid==ADMIN_ID or settings().get("staff:"+str(uid))=="support"

def media(message):
    if getattr(message,"photo",None):
        return ["photo",message.photo[-1].file_id]
    for kind in ("document","video"):
        item=getattr(message,kind,None)
        if item:
            return [kind,item.file_id]
    return None

def send_media(bot,uid,attachment):
    kind,fid=attachment
    {"photo":bot.send_photo,"document":bot.send_document,"video":bot.send_video}[kind](uid,fid)

def callback(bot,uid,msg,data,lang,states):
    ar=lang=="ar"
    def show(text,*buttons):
        render(bot,uid,msg,text,menu(*buttons))
    back=button("⬅️","ds")
    parts=data.split("|")
    act=parts[1] if len(parts)>1 else "home"
    arg=parts[2] if len(parts)>2 else ""
    if act=="home":
        states.pop(uid,None)
        keys=[button(v[0 if ar else 1],f"ds|new|{k}") for k,v in SERVICES.items()]
        show("🎨 اختر الخدمة التي ترغب بطلبها." if ar else "🎨 Choose a creative service.",*keys,button("📦 طلباتي / My requests","ds|list"),button("🏠","main"))
    elif act=="new" and arg in SERVICES:
        if arg == "cv":
            begin(bot, uid, msg, uid, "cv", lang, states)
            return
        if not active(arg):
            states.pop(uid,None)
            show("هذه الخدمة قيد التطوير. يمكنك طلب إشعار عند توفرها." if ar else "This service is paused. Get notified when it returns.",button("🔔","ds|watch|"+arg),back)
            return
        states[uid]={"mode":"brief","service":arg,"lines":[],"files":[],"key":secrets.token_hex(12)}
        prompt=settings().get("brief:"+arg) or SERVICES[arg][2 if ar else 3]
        show(escape(prompt),button("✅ إرسال الطلب / Submit","ds|submit"),button("❌","ds"))
    elif act=="watch" and arg in SERVICES:
        write("INSERT OR IGNORE INTO stock_alerts VALUES(?,?)",(uid,"design:"+arg))
        show("🔔 تم تسجيل طلب التنبيه." if ar else "🔔 Alert saved.",back)
    elif act=="submit":
        draft=states.get(uid,{})
        if draft.get("mode")!="brief": return
        service=draft["service"]
        if service == "cv":
            begin(bot, uid, msg, uid, "cv", lang, states)
            return
        if not active(service): return callback(bot,uid,msg,"ds|new|"+service,lang,states)
        if not (draft["lines"] or draft["files"]) or service=="free_avatar" and (not draft["lines"] or not any(f[0]=="photo" for f in draft["files"])):
            bot.send_message(uid,"يرجى إرفاق الصورة والتفاصيل المطلوبة." if ar else "Please send the requested details and portrait.")
            return
        now=int(time.time())
        cfg=settings()
        with transaction(write=True) as c:
            since=now-30*86400 if cfg.get("avatar_period","month")=="month" else 0
            used=c.execute("SELECT count(*) FROM design_requests WHERE user_id=? AND service_type='free_avatar' AND created_at>=?",(uid,since)).fetchone()[0]
            if service=="free_avatar" and used>=int(cfg.get("avatar_limit",1)):
                oid=None
            else:
                c.execute("INSERT OR IGNORE INTO design_requests(user_id,service_type,status,brief_text,attachments_json,created_at,updated_at,draft_key) VALUES(?,?,'pending_review',?,?,?,?,?)",
                          (uid,service,"\n".join(draft["lines"]),json.dumps(draft["files"]),now,now,draft["key"]))
                oid=c.execute("SELECT id FROM design_requests WHERE draft_key=?",(draft["key"],)).fetchone()[0]
        states.pop(uid,None)
        if oid is None:
            show("استخدمت حصتك من طلبات الصور المجانية." if ar else "You have used your free portrait allowance.",back)
            return
        show((f"✅ تم استلام طلبك بنجاح (#{oid}). سنراجع المعلومات والبيانات المرسلة، انتظرنا وسنتواصل معك قريباً." if ar else f"✅ Request #{oid} received. We will review your brief and contact you."),button("📦 متابعة الطلب / Track",f"ds|card|{oid}"))
        notify_request(bot,oid)
    elif act in ("list","queue"):
        if act=="queue" and not staff(uid): return
        page=int(arg) if arg.isdigit() else 0
        where,params=("",[]) if act=="queue" else ("WHERE user_id=?",[uid])
        items=rows(f"SELECT * FROM design_requests {where} ORDER BY id DESC LIMIT 9 OFFSET ?",(*params,page*8))
        keys=[button(f"#{r['id']} · {SERVICES[r['service_type']][0 if ar else 1]} · {STATUS[r['status']] if ar else r['status']}",f"ds|card|{r['id']}") for r in items[:8]]
        if page: keys.append(button("◀",f"ds|{act}|{page-1}"))
        if len(items)>8: keys.append(button("▶",f"ds|{act}|{page+1}"))
        show("📦 طلبات التصميم" if ar else "📦 Design requests",*keys,back)
    elif arg.isdigit():
        found=rows("SELECT * FROM design_requests WHERE id=?",(arg,))
        if not found: return
        r=found[0]
        if r["user_id"]!=uid and not staff(uid): return
        open_order=r["status"] in ("pending_review","in_progress")
        if act=="card":
            keys=[]
            if not open_order: keys.append(button("📎 ملفات الطلب / Files",f"ds|files|{arg}"))
            if open_order:
                keys.append(button("💬 رسالة / Message",f"ds|message|{arg}"))
                if staff(uid):
                    keys.extend(button(label,f"ds|{action}|{arg}") for label,action in [("🛠 بدء التنفيذ","start"),("✅ تسليم","deliver"),("❌ رفض مع سبب","reject")])
                    keys.append(button("📎 المرفقات",f"ds|files|{arg}"))
                elif r["status"]=="pending_review": keys.append(button("❌ إلغاء الطلب / Cancel",f"ds|cancel|{arg}"))
            history=rows("SELECT body FROM design_messages WHERE request_id=? ORDER BY id DESC LIMIT 4",(arg,))
            text=f"🎨 #{arg} · {SERVICES[r['service_type']][0 if ar else 1]}\n{STATUS[r['status']] if ar else r['status']}\n{escape(r['brief_text'][:1600])}"
            if len(r["brief_text"])>1600:
                keys.append(button("📄 التفاصيل / Full brief",f"ds|brief|{arg}"))
            if staff(uid):
                u=get_user(r["user_id"])
                text+=f"\n👤 {u['user_id']} @{escape(u['username'] or '—')}"
            show(text+"".join("\n💬 "+escape(h["body"][:220]) for h in reversed(history)),*keys,back)
        elif act=="brief":
            for start in range(0,len(r["brief_text"]),2600):
                bot.send_message(uid,escape(r["brief_text"][start:start+2600]))
        elif act=="files":
            for f in json.loads(r["attachments_json"]): send_media(bot,uid,f)
            for entry in rows("SELECT media FROM design_messages WHERE request_id=? AND media IS NOT NULL",(arg,)):
                attachment=json.loads(entry["media"])
                if attachment: send_media(bot,uid,attachment)
        elif act in ("start","cancel") and open_order:
            if act=="start" and not staff(uid) or act=="cancel" and uid!=r["user_id"]: return
            target="in_progress" if act=="start" else "cancelled"
            if write("UPDATE design_requests SET status=?,updated_at=unixepoch() WHERE id=? AND status='pending_review'",(target,arg)):
                bot.send_message(r["user_id"] if act=="start" else ADMIN_ID,f"🎨 #{arg} · {STATUS[target]}")
            callback(bot,uid,msg,"ds|card|"+arg,lang,states)
        elif open_order and (act=="message" or staff(uid) and act in ("deliver","reject")):
            states[uid]={"mode":"design_reply","act":act,"oid":int(arg)}
            show("أرسل الرسالة أو الملف لهذا الطلب." if ar else "Send a message or file for this request.",button("⬅️",f"ds|card|{arg}"))

def notify_request(bot,oid):
    from telebot.apihelper import ApiException
    r=rows("SELECT * FROM design_requests WHERE id=?",(oid,))[0]
    try:
        callback(bot,ADMIN_ID,None,f"ds|card|{oid}","ar",{})
        brief=r["brief_text"]
        if len(brief)>1600:
            for start in range(0,len(brief),2600): bot.send_message(ADMIN_ID,escape(brief[start:start+2600]))
        for f in json.loads(r["attachments_json"]): send_media(bot,ADMIN_ID,f)
    except (OSError,ApiException):
        write("INSERT OR IGNORE INTO push_queue(uid,kind,ref,unique_key) VALUES(?,'design',?,?)",(ADMIN_ID,str(oid),"design:"+str(oid)))


def collect(bot,m,state,states):
    uid=m.from_user.id
    text=(m.text or getattr(m,"caption",None) or "").strip()
    f=media(m)
    if len(text)>2400 or not (text or f): return
    if state["mode"]=="brief":
        if state["service"] == "cv":
            begin(bot, uid, None, uid, "cv", get_user(uid)["lang"], states)
            return
        if sum(map(len,state["lines"]))+len(text)>5000 or len(state["files"])>=10:
            bot.send_message(uid,"يرجى إرسال الطلب الحالي. / Please submit the current brief.")
            return
        if text: state["lines"].append(text)
        if f: state["files"].append(f)
        bot.send_message(uid,"📎 تم الحفظ / Saved",reply_markup=menu(button("✅ إرسال الطلب / Submit","ds|submit"),button("❌","ds")))
        return
    oid,act=state["oid"],state["act"]
    with transaction(write=True) as c:
        r=c.execute("SELECT * FROM design_requests WHERE id=?",(oid,)).fetchone()
        if not r or r["status"] not in ("pending_review","in_progress"): return
        if not staff(uid) and (uid!=r["user_id"] or act!="message"): return
        if act=="reject" and not text: return
        if act in ("reject","deliver"):
            c.execute("UPDATE design_requests SET status=?,admin_note=?,updated_at=unixepoch() WHERE id=?",("completed" if act=="deliver" else "rejected",text,oid))
        c.execute("INSERT INTO design_messages(request_id,sender,body,media) VALUES(?,?,?,?)",(oid,uid,text,json.dumps(f)))
    states.pop(uid,None)
    target=r["user_id"] if staff(uid) else ADMIN_ID
    bot.send_message(target,f"🎨 #{oid} · { {'deliver':'✅','reject':'❌','message':'💬'}[act]}\n{escape(text)}",reply_markup=menu(button("📦",f"ds|card|{oid}")))
    if f: send_media(bot,target,f)
    bot.send_message(uid,"✅")
