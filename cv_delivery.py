"""إرسال ملف السيرة (PDF أو Word) من الأدمن إلى مالك طلب CV.

الضمانات:
- المستلم هو user_id المحفوظ في الطلب المدفوع فقط، ولا يُؤخذ من أي إدخال.
- لا يُعتبر الطلب «تم التسليم» إلا بعد أن يقبل تيليغرام الإرسال.
- الفشل المؤكد (400/401/403/404/429) يسمح بإعادة المحاولة. أي نتيجة غير مؤكدة (انقطاع، مهلة، 5xx، توقف أثناء الإرسال)
  تمنع الإرسال المكرر حتى يقرّر الأدمن: «وصل» أو «لم يصل».
- الإرسال يتم بعد حجز الحالة sending في قاعدة البيانات، فالضغط المزدوج لا يرسل ملفين.
"""
import os
import secrets
import time
from html import escape

from telebot import apihelper

import commerce_db as store
import database as db
from database import transaction
from platform_db import rows, write

EXTENSIONS = {
    ".pdf": {"application/pdf"},
    ".doc": {"application/msword"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
}
GENERIC_MIME = {"application/octet-stream", "application/zip", "application/x-zip-compressed", ""}
MAX_SIZE = 20 * 1024 * 1024
OPEN_STATES = ("cv_details", "cv_work")
DEFINITE = {400, 401, 403, 404, 429}
STALE_SENDING = 120
LABELS = {"sending": "قيد الإرسال", "sent": "أُرسل ✅", "failed": "فشل مؤكد، يمكن إعادة المحاولة",
          "uncertain": "غير مؤكد ⚠️ لا تُعد الإرسال قبل التحقق"}


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS cv_deliveries (
        order_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, state TEXT NOT NULL, file_id TEXT,
        file_unique_id TEXT, file_name TEXT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
        message_id INTEGER, updated INTEGER NOT NULL)""")


def status(oid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM cv_deliveries WHERE order_id=?", (oid,)).fetchone()


def open_order(conn, oid):
    row = conn.execute("SELECT * FROM checkout_orders WHERE id=?", (oid,)).fetchone()
    if not row or row["product_id"] != "cv" or row["paid"] is None or row["state"] not in OPEN_STATES:
        return None
    return row


def check_document(document):
    """يرجع (name, None) أو (None, سبب بالعربية). المدخلات من الأدمن لكنها تُفحص لتفادي الأخطاء."""
    if document is None:
        return None, "أرسل الملف كمستند (Document) وليس كصورة أو نص."
    name = os.path.basename(str(getattr(document, "file_name", "") or "cv"))[:120]
    ext = os.path.splitext(name)[1].lower()
    if ext not in EXTENSIONS:
        return None, "الصيغ المقبولة: PDF أو DOC أو DOCX."
    mime = str(getattr(document, "mime_type", "") or "").lower()
    if mime not in EXTENSIONS[ext] and mime not in GENERIC_MIME:
        return None, "نوع الملف لا يطابق امتداده."
    size = getattr(document, "file_size", None)
    if not isinstance(size, int) or size <= 0 or size > MAX_SIZE:
        return None, "حجم الملف غير صالح (الحد 20 ميغابايت)."
    return name, None


def complete_order(oid, actor, name):
    """يحوّل الطلب إلى تم التسليم ويضع إشعاراً للعميل مرة واحدة. آمن عند التكرار."""
    changed = store.transition(oid, OPEN_STATES, "delivered", actor, action="cv_file_sent",
                               reason=f"cv_file:{name}"[:200], delivered=int(time.time()))
    row = store.order(oid)
    if row:
        write("INSERT OR IGNORE INTO push_queue(uid,kind,ref,unique_key) VALUES(?,'order',?,?)",
              (row["user_id"], str(oid), f"cv_delivered:{oid}"))
    return changed


def claim(oid, actor, file_id, unique_id, name, force=False):
    """يحجز الإرسال. يرجع (النتيجة، user_id). النتيجة ok أو order/sent/busy/uncertain."""
    now = int(time.time())
    with transaction(write=True) as conn:
        order = open_order(conn, oid)
        current = conn.execute("SELECT * FROM cv_deliveries WHERE order_id=?", (oid,)).fetchone()
        if current and current["state"] == "sending" and current["updated"] < now - STALE_SENDING:
            conn.execute("UPDATE cv_deliveries SET state='uncertain',error='stale_sending',updated=? WHERE order_id=?", (now, oid))
            current = conn.execute("SELECT * FROM cv_deliveries WHERE order_id=?", (oid,)).fetchone()
        if current and current["state"] == "sent":
            return "sent", current["user_id"]
        if not order:
            return "order", None
        if current and current["state"] == "sending":
            return "busy", order["user_id"]
        if current and current["state"] == "uncertain" and not force:
            return "uncertain", order["user_id"]
        conn.execute("""INSERT INTO cv_deliveries(order_id,user_id,state,file_id,file_unique_id,file_name,attempts,updated)
            VALUES(?,?,'sending',?,?,?,1,?) ON CONFLICT(order_id) DO UPDATE SET state='sending',file_id=excluded.file_id,
            file_unique_id=excluded.file_unique_id,file_name=excluded.file_name,attempts=attempts+1,error=NULL,updated=excluded.updated""",
                     (oid, order["user_id"], file_id, unique_id, name, now))
        store.audit(conn, "cv_file_attempt", actor, oid, detail=name[:100])
        return "ok", order["user_id"]


def mark(oid, state, error=None, message_id=None):
    with transaction(write=True) as conn:
        conn.execute("UPDATE cv_deliveries SET state=?,error=?,message_id=COALESCE(?,message_id),updated=? WHERE order_id=?",
                     (state, error, message_id, int(time.time()), oid))


def reconcile(oid, actor):
    """إذا حُفظ الإرسال كناجح ثم توقف النظام قبل إغلاق الطلب، يُكمَل الإغلاق دون إرسال ثانٍ."""
    row = status(oid)
    if row and row["state"] == "sent":
        complete_order(oid, actor, row["file_name"] or "cv")
        return True
    return False


def send(bot, oid, actor, file_id, unique_id, name, force=False):
    """يرجع (sent|failed|uncertain|busy|order, تفاصيل)."""
    from locales import t
    file_ref = file_id if isinstance(file_id, str) else unique_id
    result, owner = claim(oid, actor, file_ref, unique_id, name, force)
    if result == "sent":
        reconcile(oid, actor)
        return "sent", "already"
    if result != "ok":
        return result, None
    lang = db.get_user(owner)["lang"]
    try:
        message = bot.send_document(owner, file_id, caption=t(lang, "cv_file_caption", order_id=oid))
    except apihelper.ApiTelegramException as exc:
        code = getattr(exc, "error_code", None)
        if code in DEFINITE:
            mark(oid, "failed", str(code))
            return "failed", code
        mark(oid, "uncertain", f"api:{code}")
        return "uncertain", code
    except Exception as exc:
        mark(oid, "uncertain", type(exc).__name__)
        return "uncertain", None
    mark(oid, "sent", None, getattr(message, "message_id", None))
    complete_order(oid, actor, name)
    with transaction(write=True) as conn:
        store.audit(conn, "cv_file_sent", actor, oid, new=name[:100])
    return "sent", None


def confirm_arrived(oid, actor):
    """الأدمن تحقق أن العميل استلم الملف رغم عدم التأكد: إغلاق بدون إرسال."""
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM cv_deliveries WHERE order_id=?", (oid,)).fetchone()
        stale = row and row["state"] == "sending" and row["updated"] < int(time.time()) - STALE_SENDING
        if not row or (row["state"] != "uncertain" and not stale):
            return False
        conn.execute("UPDATE cv_deliveries SET state='sent',error='admin_confirmed',updated=? WHERE order_id=?", (int(time.time()), oid))
        store.audit(conn, "cv_file_confirmed", actor, oid)
    complete_order(oid, actor, row["file_name"] or "cv")
    return True


# ───────────────────────── واجهة الأدمن ─────────────────────────

NOTES = {"failed": "لم يُرسل الملف: رفض تيليغرام الإرسال (قد يكون العميل حظر البوت). الطلب ما زال مفتوحاً ويمكنك المحاولة لاحقاً أو التواصل مع العميل.",
         "uncertain": "تعذّر التأكد من وصول الملف. لم يُغلق الطلب. تحقق من العميل ثم اختر «وصل» أو «لم يصل».",
         "busy": "هناك إرسال قيد التنفيذ لهذا الطلب. انتظر قليلاً ثم حدّث البطاقة.",
         "order": "هذا الطلب لا يقبل إرسال سيرة الآن (غير مدفوع، أو ليس طلب CV، أو أُغلق).",
         "sent": "أُرسل الملف مسبقاً ولن يُرسل مرة ثانية.",
         "ok": "✅ أُرسل الملف للعميل وأُغلق الطلب كمسلَّم."}


def card(panel, bot, chat, msg, oid, note=""):
    import ops_console
    ops_console.card(panel, bot, chat, msg, oid, note)


def ask(panel, bot, chat, msg, view, oid, force=False):
    with transaction() as conn:
        order = open_order(conn, oid)
    if not order:
        card(panel, bot, chat, msg, oid, NOTES["order"])
        return
    current = status(oid)
    if current and current["state"] == "sent":
        reconcile(oid, view.get("uid"))
        card(panel, bot, chat, msg, oid, NOTES["sent"])
        return
    if current and current["state"] == "sending" and current["updated"] >= int(time.time()) - STALE_SENDING and not force:
        card(panel, bot, chat, msg, oid, NOTES["busy"])
        return
    if current and current["state"] in {"uncertain", "sending"} and not force:
        keys = panel.menu(panel.button("✅ وصل للعميل: إغلاق الطلب", f"cvfileok|{oid}"),
                          panel.button("🔁 لم يصل: إرسال ملف من جديد", f"cvfilere|{oid}"),
                          panel.button("⬅️ البطاقة", f"opview|{oid}"))
        panel.screen(bot, chat, msg, f"⚠️ <b>إرسال غير مؤكد · #{oid}</b>\n{NOTES['uncertain']}", keys)
        return
    view.update(action="cv_file", cv_oid=oid, cv_force=bool(force))
    panel.screen(bot, chat, msg, f"📎 <b>إرسال CV للزبون · #{oid}</b>\nالعميل: <code>{order['user_id']}</code>\n"
                 "أرسل الآن ملف PDF أو Word كمستند (Document). ستظهر معاينة قبل الإرسال.",
                 panel.menu(panel.button("⬅️ إلغاء", f"opview|{oid}")))


def admin_file(panel, bot, message, view):
    """يُستدعى من handle_text عندما تكون الحالة cv_file."""
    oid = view.get("cv_oid")
    name, problem = check_document(getattr(message, "document", None))
    if problem:
        bot.send_message(message.chat.id, f"❌ {problem}")
        return
    with transaction() as conn:
        order = open_order(conn, oid) if isinstance(oid, int) else None
    if not order:
        view.pop("action", None)
        bot.send_message(message.chat.id, NOTES["order"])
        return
    document = message.document
    token = secrets.token_hex(6)
    view["cv_file_preview"] = [token, oid, document.file_id, getattr(document, "file_unique_id", ""), name, bool(view.get("cv_force"))]
    view.pop("action", None)
    size_kb = max(1, document.file_size // 1024)
    bot.send_message(message.chat.id,
                     f"📎 <b>معاينة قبل الإرسال</b>\nالطلب: #{oid}\nالمستلم: العميل <code>{order['user_id']}</code> (مالك الطلب)\n"
                     f"الملف: {escape(name)} · {size_kb} KB\nبعد قبول تيليغرام للإرسال يُغلق الطلب كمسلَّم.",
                     reply_markup=panel.menu(panel.button("✅ تأكيد الإرسال", f"cvfilego|{token}"),
                                             panel.button("⬅️ إلغاء", f"opview|{oid}")))


def callback(panel, bot, call, view, action, args):
    chat, msg, uid = call.message.chat.id, call.message.message_id, call.from_user.id
    view["uid"] = uid
    if action == "cvfile":
        ask(panel, bot, chat, msg, view, int(args[0]))
    elif action == "cvfilere":
        ask(panel, bot, chat, msg, view, int(args[0]), force=True)
    elif action == "cvfileok":
        oid = int(args[0])
        done = confirm_arrived(oid, uid)
        card(panel, bot, chat, msg, oid, "تم إغلاق الطلب كمسلَّم بعد تأكيدك." if done else NOTES["order"])
    elif action == "cvfilego":
        draft = view.pop("cv_file_preview", None)
        if not draft or draft[0] != args[0]:
            bot.send_message(chat, "انتهت صلاحية المعاينة. افتح الطلب وابدأ الإرسال من جديد.")
            return
        _, oid, file_id, unique_id, name, force = draft
        result, detail = send(bot, oid, uid, file_id, unique_id, name, force)
        note = NOTES["ok"] if result == "sent" and detail is None else NOTES.get(result, "")
        if result == "failed" and detail:
            note += f" (رمز {detail})"
        card(panel, bot, chat, msg, oid, note)
