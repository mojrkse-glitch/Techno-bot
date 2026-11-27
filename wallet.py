import os
import re
import time
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_DOWN

from database import change_balance, transaction

METHODS = {
    "syriatel": ("Syriatel Cash", "SYP", "0934053425", "رقم العملية", "Transaction number"),
    "sham_syp": ("Sham Cash (SYP)", "SYP", "a7eef9aae0ffbb0124d99c6c91c85738", "رقم العملية", "Transaction number"),
    "sham_usd": ("Sham Cash (USD)", "USD", "a7eef9aae0ffbb0124d99c6c91c85738", "رقم العملية", "Transaction number"),
    "usdt": ("USDT (BEP20)", "USDT", "0xdc30bed97193cd8d97aa341129de86729acfeb84", "Order ID أو TxID", "Order ID or TxID"),
}
CENT = Decimal("0.01")


def enabled():
    return os.getenv("TEC_PAY_MANUAL_WALLET_ENABLED", "").strip() == "1"


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS wallet_topups (
        id INTEGER PRIMARY KEY, nonce TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, method TEXT NOT NULL,
        currency TEXT NOT NULL, amount TEXT NOT NULL, rate TEXT NOT NULL, credit TEXT NOT NULL,
        destination TEXT NOT NULL, reference TEXT NOT NULL, reference_key TEXT NOT NULL,
        provider_group TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
        submitted INTEGER NOT NULL, reviewed INTEGER, reviewer INTEGER, reason TEXT,
        verified_reference TEXT
    )""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS wallet_topups_active_ref
        ON wallet_topups(provider_group,reference_key) WHERE state IN ('pending','approved')""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS wallet_topups_verified_ref
        ON wallet_topups(provider_group,verified_reference) WHERE verified_reference IS NOT NULL""")
    conn.execute("CREATE INDEX IF NOT EXISTS wallet_topups_queue ON wallet_topups(state,user_id,id)")


def parse_amount(raw, currency):
    value = unicodedata.normalize("NFKC", raw.strip()).replace("٫", ".")
    if not re.fullmatch(r"\d{1,9}(?:\.\d{1,6})?", value):
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    scale = {"SYP": Decimal("1"), "USD": CENT, "USDT": Decimal("0.000001")}[currency]
    if amount <= 0 or amount != amount.quantize(scale):
        return None
    return amount


def rate_for(currency, conn=None):
    from commerce_db import settings

    if currency in {"USD", "USDT"}:
        return Decimal("1")
    if conn is None:
        saved = settings().get("wallet_syp_per_usd")
    else:
        row = conn.execute("SELECT value FROM checkout_settings WHERE key='wallet_syp_per_usd'").fetchone()
        saved = row[0] if row else None
    raw = saved or os.getenv("TEC_PAY_SYP_PER_USD", "")
    try:
        rate = Decimal(raw)
        return rate if rate.is_finite() and 0 < rate <= Decimal("1000000000") else None
    except InvalidOperation:
        return None


def syp_label(usd, rate, lang):
    if usd is None or rate is None:
        return ""
    amount = (Decimal(usd) * rate).to_integral_value(rounding=ROUND_CEILING)
    return f"≈ {amount:,} {'ل.س' if lang == 'ar' else 'SYP'}"


def credit_for(amount, rate):
    return (amount / rate).quantize(CENT, rounding=ROUND_DOWN)


def reference_value(raw):
    normalized = unicodedata.normalize("NFKC", raw.strip())
    chars = (str(unicodedata.decimal(char)) if char.isdecimal() else char for char in normalized)
    value = "".join(chars)
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]{3,120}", value) else None


def tx_hash(raw):
    value = reference_value(raw)
    if value and re.fullmatch(r"(?:0x)?[0-9a-fA-F]{64}", value, re.IGNORECASE):
        value = value.lower()
        return value if value.startswith("0x") else f"0x{value}"
    return None


def topup_reference(source, raw):
    value = raw.strip()
    if source == "usdt":
        return value.lower() if re.fullmatch(r"0x[0-9a-fA-F]{64}", value) else None

    value = "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in value)
    patterns = {"syriatel": r"600[0-9]{9}", "sham_syp": r"[0-9]{9}", "sham_usd": r"[0-9]{9}"}
    pattern = patterns.get(source)
    return value if pattern and re.fullmatch(pattern, value) else None


def submit(uid, source, amount, rate, reference, nonce, destination_snapshot=None):
    from commerce_db import audit

    reference = topup_reference(source, reference)
    if reference is None:
        return None, "invalid_reference"

    _, currency, destination, _, _ = METHODS[source]
    from commerce_db import settings
    destination=destination_snapshot or settings().get("dest:"+source,destination)
    group = "sham" if source.startswith("sham_") else source
    key = reference
    credit = credit_for(amount, rate)
    if credit <= 0:
        return None, "small"
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM wallet_topups WHERE nonce=?", (nonce,)).fetchone()
        if row:
            return row, "existing"
        if conn.execute("""SELECT 1 FROM wallet_topups WHERE provider_group=? AND
            ((reference_key=? AND state IN ('pending','approved')) OR verified_reference=?)""",
                        (group, key, key)).fetchone():
            return None, "duplicate"
        cur = conn.execute("""INSERT INTO wallet_topups(nonce,user_id,method,currency,amount,rate,credit,
            destination,reference,reference_key,provider_group,submitted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                           (nonce, uid, source, currency, str(amount), str(rate), str(credit), destination, reference,
                            key, group, int(time.time())))
        audit(conn, "topup_requested", uid, detail=f"topup:{cur.lastrowid}", new="pending")
        return conn.execute("SELECT * FROM wallet_topups WHERE id=?", (cur.lastrowid,)).fetchone(), "created"


def get(oid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM wallet_topups WHERE id=?", (oid,)).fetchone()


def rows(uid=None, page=0, pending=False):
    with transaction() as conn:
        return conn.execute("""SELECT * FROM wallet_topups WHERE (? IS NULL OR user_id=?)
                               AND (?=0 OR state='pending') ORDER BY id DESC LIMIT 9 OFFSET ?""",
                            (uid, uid, int(pending), page * 8)).fetchall()


def pending_count():
    with transaction() as conn:
        return conn.execute("SELECT COUNT(*) FROM wallet_topups WHERE state='pending'").fetchone()[0]


def decide(oid, actor, decision, *, received=None, verified=None, reason=""):
    from commerce_db import audit

    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM wallet_topups WHERE id=?", (oid,)).fetchone()
        if not row or row["state"] != "pending":
            return "closed"
        if decision not in {"approved", "rejected"}:
            return "invalid"
        if decision == "approved":
            if received != Decimal(row["amount"]):
                return "mismatch"
            key = row["reference_key"]
            if row["method"] == "usdt":
                key = tx_hash(verified or "")
                submitted_hash = tx_hash(row["reference"])
                if not key or submitted_hash and key != submitted_hash:
                    return "hash"
            if conn.execute("""SELECT 1 FROM wallet_topups WHERE provider_group=? AND id!=? AND state='approved'
                AND (verified_reference=? OR reference_key=?)""", (row["provider_group"], oid, key, key)).fetchone():
                return "duplicate"
            change_balance(conn, row["user_id"], Decimal(row["credit"]))
        else:
            key = None
            if not reason:
                return "invalid"
        conn.execute("""UPDATE wallet_topups SET state=?,reviewer=?,reviewed=?,reason=?,verified_reference=?
                        WHERE id=?""", (decision, actor, int(time.time()), reason, key, oid))
        audit(conn, f"topup_{decision}", actor, old="pending", new=decision, detail=f"topup:{oid}")
        return decision
