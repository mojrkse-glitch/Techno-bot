"""استلام بيانات السيرة الذاتية من Telegram Mini App.

التدفق: تأكيد الدفع أو بطاقة طلب CV مدفوع ← زر Mini App ← token قصير العمر أحادي الاستخدام في رابط الزر ←
الواجهة ترسل عبر sendData ← handler web_app_data هنا يتحقق ويحفظ ويستهلك التوكن في معاملة SQLite واحدة.
كل ما يصل من العميل غير موثوق. لا تُسجَّل بيانات السيرة في أي سجل نصي.
"""
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import urlencode, urlsplit

from telebot import types

import commerce_db as store
import database as db
from database import transaction

SCHEMA_VERSION = 1
MAX_BYTES = 4096
TOKEN_TTL = 1800
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")
CONTROL = re.compile(r"[\x00-\x1f\x7f\u2028\u2029]")
EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")
PHONE = re.compile(r"^\+[1-9]\d{6,14}$")
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
PRESENT = {"ar": "حتى الآن", "en": "Present"}
LEVELS = {
    "ar": ("اللغة الأم", "ممتاز", "جيد جداً", "جيد", "متوسط", "مبتدئ"),
    "en": ("Native", "Excellent", "Very Good", "Good", "Intermediate", "Beginner"),
}
TITLES = {
    "ar": {"professional_summary": "الملخص المهني", "work_experience": "الخبرة العملية", "education": "التعليم",
           "skills": "المهارات", "languages": "اللغات"},
    "en": {"professional_summary": "Professional Summary", "work_experience": "Work Experience",
           "education": "Education", "skills": "Skills", "languages": "Languages"},
}
PERSONAL = ("full_name", "job_title", "location", "phone", "email")
LIMITS = {"experience": 6, "education": 5, "languages": 6, "skills": 30, "responsibilities": 10}


class Invalid(Exception):
    """يحمل مسار الحقل (من كودنا نحن وليس من المدخلات) لعرضه في رسالة الخطأ."""
    def __init__(self, path):
        super().__init__(path)
        self.path = path


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS cv_webapp_tokens (
        token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, order_id INTEGER NOT NULL,
        created INTEGER NOT NULL, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cv_submissions (
        order_id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, resume_language TEXT NOT NULL,
        payload TEXT NOT NULL, submitted_at INTEGER NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS cv_token_order ON cv_webapp_tokens(order_id,used)")


# ───────────────────────── الإعداد والرابط ─────────────────────────

def webapp_url():
    value = os.getenv("CV_WEBAPP_URL", "").strip()
    if not value:
        try:
            import app_settings as config
            value = str(getattr(config, "CV_WEBAPP_URL", "") or "").strip()
        except ImportError:
            value = ""
    parts = urlsplit(value)
    return value if parts.scheme == "https" and parts.netloc and not parts.fragment else None


def eligible(conn, uid, oid):
    """طلب CV مدفوع، يخص المرسل، بانتظار المعلومات، ولم تُرسل له بيانات من قبل."""
    row = conn.execute("SELECT * FROM checkout_orders WHERE id=?", (oid,)).fetchone()
    if not row or row["product_id"] != "cv" or row["user_id"] != uid or row["state"] not in {"cv_details", "cv_work"} or row["paid"] is None:
        return None
    if conn.execute("SELECT 1 FROM cv_submissions WHERE order_id=?", (oid,)).fetchone():
        return None
    return row


def submission(oid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM cv_submissions WHERE order_id=?", (oid,)).fetchone()


def issue_token(uid, oid, reuse=False):
    """إنشاء توكن، مع إمكانية إعادة استخدام رابط صالح للإشعار التلقائي."""
    now = int(time.time())
    with transaction(write=True) as conn:
        if not eligible(conn, uid, oid):
            return None
        if reuse:
            existing = conn.execute("""SELECT token FROM cv_webapp_tokens
                WHERE order_id=? AND used=0 AND expires>=? ORDER BY created DESC LIMIT 1""", (oid, now)).fetchone()
            if existing:
                return existing["token"]
        conn.execute("UPDATE cv_webapp_tokens SET used=1 WHERE order_id=? AND used=0", (oid,))
        conn.execute("DELETE FROM cv_webapp_tokens WHERE expires<?", (now - 86400,))
        token = secrets.token_urlsafe(9)
        conn.execute("INSERT INTO cv_webapp_tokens(token,user_id,order_id,created,expires) VALUES(?,?,?,?,?)",
                     (token, uid, oid, now, now + TOKEN_TTL))
        return token


# ───────────────────────── التحقق (المدخلات غير موثوقة) ─────────────────────────

def text(value, path, maximum, required=False):
    if not isinstance(value, str):
        raise Invalid(path)
    value = value.strip()
    if CONTROL.search(value) or len(value) > maximum or (required and not value):
        raise Invalid(path)
    return value


def keys_exact(value, expected, path):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise Invalid(path)
    return value


def items(value, path, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise Invalid(path)
    return value


def normalize_phone(value):
    value = re.sub(r"[\s\-().]", "", value.translate(DIGITS))
    if value.startswith("00"):
        value = "+" + value[2:]
    return value


def validate_cv(cv, lang):
    cv = keys_exact(cv, ("personal_info", "professional_summary", "work_experience", "education", "skills", "languages"), "cvData")
    info = keys_exact(cv["personal_info"], PERSONAL, "personal_info")
    clean_info = {key: text(info[key], f"personal_info.{key}", 120, True) for key in ("full_name", "job_title", "location")}
    phone = normalize_phone(text(info["phone"], "personal_info.phone", 40, True))
    email = text(info["email"], "personal_info.email", 254, True)
    if not PHONE.match(phone):
        raise Invalid("personal_info.phone")
    if not EMAIL.match(email):
        raise Invalid("personal_info.email")
    clean = {"personal_info": {"full_name": clean_info["full_name"], "job_title": clean_info["job_title"],
                               "location": clean_info["location"], "phone": phone, "email": email}}
    clean["professional_summary"] = text(cv["professional_summary"], "professional_summary", 800)
    work = []
    for i, job in enumerate(items(cv["work_experience"], "work_experience", LIMITS["experience"])):
        path = f"work_experience[{i}]"
        job = keys_exact(job, ("job_title", "company", "start_date", "end_date", "responsibilities"), path)
        duties = [text(line, f"{path}.responsibilities", 200, True)
                  for line in items(job["responsibilities"], f"{path}.responsibilities", LIMITS["responsibilities"])]
        work.append({"job_title": text(job["job_title"], f"{path}.job_title", 100, True),
                     "company": text(job["company"], f"{path}.company", 100, True),
                     "start_date": text(job["start_date"], f"{path}.start_date", 40, True),
                     "end_date": text(job["end_date"], f"{path}.end_date", 40, True), "responsibilities": duties})
    clean["work_experience"] = work
    study = []
    for i, entry in enumerate(items(cv["education"], "education", LIMITS["education"])):
        path = f"education[{i}]"
        entry = keys_exact(entry, ("degree", "institution", "year_or_status"), path)
        study.append({"degree": text(entry["degree"], f"{path}.degree", 120, True),
                      "institution": text(entry["institution"], f"{path}.institution", 120, True),
                      "year_or_status": text(entry["year_or_status"], f"{path}.year_or_status", 60)})
    clean["education"] = study
    skills, seen = [], set()
    for skill in items(cv["skills"], "skills", LIMITS["skills"]):
        skill = text(skill, "skills", 50, True)
        if skill.casefold() not in seen:
            seen.add(skill.casefold())
            skills.append(skill)
    clean["skills"] = skills
    spoken = []
    for i, entry in enumerate(items(cv["languages"], "languages", LIMITS["languages"])):
        path = f"languages[{i}]"
        entry = keys_exact(entry, ("language", "level"), path)
        level = text(entry["level"], f"{path}.level", 30, True)
        if level not in LEVELS[lang]:
            raise Invalid(f"{path}.level")
        spoken.append({"language": text(entry["language"], f"{path}.language", 50, True), "level": level})
    clean["languages"] = spoken
    return clean


def parse(raw):
    """يرجع (token, lang, cvData المنظّف). يرمي Invalid عند أي مخالفة."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_BYTES:
        raise Invalid("size")
    try:
        data = json.loads(raw)
    except ValueError:
        raise Invalid("json")
    data = keys_exact(data, ("token", "resume_language", "cvData"), "payload")
    token, lang = data["token"], data["resume_language"]
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        raise Invalid("token")
    if not isinstance(lang, str) or lang not in LEVELS:
        raise Invalid("resume_language")
    return token, lang, validate_cv(data["cvData"], lang)


# ───────────────────────── التصدير والحفظ ─────────────────────────

def build_export(oid, lang, cv, submitted_at):
    titles = TITLES[lang]
    info = cv["personal_info"]
    answers = {**info, "professional_summary": cv["professional_summary"], "skills": cv["skills"]}
    sections = [{"id": key, "title": titles[key], "content": cv[key]}
                for key in ("professional_summary", "work_experience", "education", "skills", "languages")]
    return {"schema_version": SCHEMA_VERSION, "order_id": oid, "resume_language": lang,
            "submitted_at": datetime.fromtimestamp(submitted_at, timezone.utc).isoformat(timespec="seconds"),
            "answers": answers, "sections": sections, "cvData": cv}


def save(uid, token, lang, cv):
    """يستهلك التوكن ويحفظ البيانات في معاملة واحدة. يرجع (order_id, None) أو (None, سبب)."""
    now = int(time.time())
    try:
        with transaction(write=True) as conn:
            row = conn.execute("SELECT * FROM cv_webapp_tokens WHERE token=?", (token,)).fetchone()
            if not row or row["user_id"] != uid:
                return None, "token"
            if row["used"]:
                return None, "used"
            if row["expires"] < now:
                return None, "expired"
            oid = row["order_id"]
            if conn.execute("SELECT 1 FROM cv_submissions WHERE order_id=?", (oid,)).fetchone():
                return None, "duplicate"
            if not eligible(conn, uid, oid):
                return None, "order"
            if conn.execute("UPDATE cv_webapp_tokens SET used=1 WHERE token=? AND used=0", (token,)).rowcount != 1:
                return None, "used"
            payload = json.dumps(build_export(oid, lang, cv, now), ensure_ascii=False)
            conn.execute("INSERT INTO cv_submissions(order_id,user_id,resume_language,payload,submitted_at) VALUES(?,?,?,?,?)",
                         (oid, uid, lang, payload, now))
            store.audit(conn, "cv_miniapp_submit", uid, oid, new=lang)
            return oid, None
    except sqlite3.IntegrityError:
        return None, "duplicate"


# ───────────────────────── واجهة البوت ─────────────────────────

REASONS = {"token": "cv_app_err_token", "used": "cv_app_err_used", "expired": "cv_app_err_expired",
           "order": "cv_app_err_order", "duplicate": "cv_app_err_used"}


def offer(bot, uid, chat_id, oid, lang, *, just_paid=False):
    """إرسال زر web_app في لوحة مفاتيح الردّ للمحادثة الخاصة."""
    from locales import t
    if chat_id != uid:
        return False
    url = webapp_url()
    if not url:
        if just_paid:
            with transaction() as conn:
                if not eligible(conn, uid, oid):
                    return False
            bot.send_message(chat_id, t(lang, "cv_app_paid_receipt", order_id=oid) + "\n" +
                             t(lang, "cv_app_unavailable"))
        else:
            bot.send_message(chat_id, t(lang, "cv_app_unavailable"))
        return False
    token = issue_token(uid, oid, reuse=True)
    if not token:
        if not just_paid:
            bot.send_message(chat_id, t(lang, "cv_app_err_order"))
        return False
    link = f"{url}{'&' if '?' in url else '?'}{urlencode({'t': token, 'o': oid})}"
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    button = "cv_app_launch" if just_paid else "cv_app_button_short"
    message = "cv_app_paid" if just_paid else "cv_app_open"
    keyboard.add(types.KeyboardButton(t(lang, button), web_app=types.WebAppInfo(url=link)))
    keyboard.add(types.KeyboardButton(("⬅️ رجوع" if lang == "ar" else "⬅️ Back") + f" · #{oid}"))
    bot.send_message(chat_id, t(lang, message, order_id=oid), reply_markup=keyboard)
    return True


def handle(bot, message):
    """handler الرسائل من نوع web_app_data."""
    from locales import t
    import admin_panel
    from app_settings import ADMIN_ID
    chat, sender = message.chat, message.from_user
    if chat.type != "private" or not sender or sender.id != chat.id:
        return
    account = db.get_user(sender.id)
    if account["is_banned"]:
        return
    lang = account["lang"]
    remove = types.ReplyKeyboardRemove()
    web = getattr(message, "web_app_data", None)
    try:
        token, resume_lang, cv = parse(getattr(web, "data", None))
    except Invalid as exc:
        bot.send_message(chat.id, t(lang, "cv_app_err_invalid", field=exc.path), reply_markup=remove)
        return
    oid, reason = save(sender.id, token, resume_lang, cv)
    if not oid:
        bot.send_message(chat.id, t(lang, REASONS[reason]), reply_markup=remove)
        return
    bot.send_message(chat.id, t(lang, "cv_app_saved", order_id=oid), reply_markup=remove)
    admin_panel.notify(bot, ADMIN_ID, f"📄 وصلت بيانات سيرة ذاتية عبر Mini App · طلب <b>#{oid}</b>\n"
                       f"اللغة: {resume_lang}",
                       reply_markup=admin_panel.menu(admin_panel.button("فتح الطلب", f"opview|{oid}"),
                                                     admin_panel.button("⬇️ تنزيل JSON", f"cvjson|{oid}")))


def send_json(bot, chat_id, oid):
    """زر تنزيل JSON للأدمن: يرسل ملف التصدير كما حُفظ."""
    row = submission(oid)
    if not row:
        bot.send_message(chat_id, "لا توجد بيانات Mini App لهذا الطلب.")
        return False
    file = BytesIO(row["payload"].encode("utf-8"))
    file.name = f"cv_order_{oid}.json"
    bot.send_document(chat_id, file, caption=f"📄 بيانات السيرة · طلب #{oid} · {row['resume_language']}")
    return True
