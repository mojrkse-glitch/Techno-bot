import json
import secrets
from decimal import Decimal
import time

from database import change_balance, transaction

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS checkout_orders (
        id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, user_id INTEGER NOT NULL, product_id TEXT NOT NULL,
        name TEXT NOT NULL, quantity INTEGER NOT NULL, stars INTEGER NOT NULL, quote_usd TEXT,
        body TEXT, state TEXT NOT NULL DEFAULT 'invoice', expires INTEGER NOT NULL, approval TEXT,
        charge TEXT UNIQUE, supplier_id TEXT, actual_usd TEXT, delivery TEXT, reason TEXT, supplier_quantity INTEGER,
        created INTEGER NOT NULL, updated INTEGER NOT NULL, paid INTEGER, delivered INTEGER,
        refunded INTEGER, due INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS checkout_customer ON checkout_orders(user_id,id)",
    "CREATE INDEX IF NOT EXISTS checkout_queue ON checkout_orders(state,due,id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS checkout_supplier ON checkout_orders(supplier_id) WHERE supplier_id IS NOT NULL",
    """CREATE TABLE IF NOT EXISTS checkout_events (
        id INTEGER PRIMARY KEY, order_id INTEGER, actor INTEGER, action TEXT NOT NULL,
        old TEXT, new TEXT, detail TEXT, at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS checkout_event_order ON checkout_events(order_id,id)",
    """CREATE TABLE IF NOT EXISTS checkout_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS catalog_edits (
        product_id TEXT PRIMARY KEY, data TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL UNIQUE, user_id INTEGER NOT NULL,
        category TEXT NOT NULL DEFAULT 'payment', status TEXT NOT NULL DEFAULT 'open',
        message TEXT, reply TEXT, updated INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS checkout_settlements (
        order_id INTEGER PRIMARY KEY, usd TEXT NOT NULL, reference TEXT NOT NULL, recorded INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS unmatched_payments (
        charge TEXT PRIMARY KEY, user_id INTEGER, token TEXT, amount INTEGER, currency TEXT, at INTEGER
    )""",
)


def init():
    with transaction(write=True) as conn:
        for statement in SCHEMA:
            conn.execute(statement)
        fields = {row["name"] for row in conn.execute("PRAGMA table_info(checkout_orders)")}
        for key, definition in (("supplier_quantity", "INTEGER"), ("wallet_amount", "TEXT"), ("wallet_syp_rate", "TEXT")):
            if key not in fields:
                conn.execute(f"ALTER TABLE checkout_orders ADD COLUMN {key} {definition}")
        from wallet import init as init_wallet
        init_wallet(conn)
        import platform_db
        platform_db.init(conn)
        import cv_pipeline
        cv_pipeline.init(conn)
        migrate_legacy_cv(conn)


def migrate_legacy_cv(conn):
    # Legacy open CV orders already debited the wallet. Move fulfillment only.
    for row in conn.execute("""SELECT * FROM orders WHERE kind='cv'
            AND status IN ('awaiting_details','in_progress')""").fetchall():
        now = int(time.time())
        cur = conn.execute("""INSERT OR IGNORE INTO checkout_orders
            (token,user_id,product_id,name,quantity,stars,state,expires,created,updated,paid,wallet_amount)
            VALUES(?,?,'cv',?,1,0,?,?,?,?,?,?)""",
            (f"legacy-cv-{row['id']}", row["user_id"], row["product_name"],
             "cv_work" if row["status"] == "in_progress" else "cv_details",
             now, now, now, now, row["sell_price"]))
        conn.execute("UPDATE orders SET status='migrated' WHERE id=?", (row["id"],))
        if cur.rowcount:
            audit(conn, "cv_legacy_migration", None, cur.lastrowid, detail=str(row["id"]))


def legacy_cv_order(oid, uid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM checkout_orders WHERE token=? AND user_id=?",
                            (f"legacy-cv-{oid}", uid)).fetchone()


def audit(conn, action, actor=None, oid=None, old=None, new=None, detail=None):
    conn.execute("""INSERT INTO checkout_events(order_id,actor,action,old,new,detail,at)
                    VALUES(?,?,?,?,?,?,?)""", (oid, actor, action, old, new, detail, int(time.time())))


def events(oid=None, limit=12):
    with transaction() as conn:
        return conn.execute("""SELECT * FROM checkout_events WHERE (? IS NULL OR order_id=?)
                               ORDER BY id DESC LIMIT ?""", (oid, oid, limit)).fetchall()


def settings():
    with transaction() as conn:
        return dict(conn.execute("SELECT key,value FROM checkout_settings").fetchall())


def set_setting(key, value, actor):
    with transaction(write=True) as conn:
        old = conn.execute("SELECT value FROM checkout_settings WHERE key=?", (key,)).fetchone()
        conn.execute("INSERT INTO checkout_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, str(value)))
        audit(conn, "setting", actor, old=old[0] if old else None, new=str(value), detail=key)


def edits():
    with transaction() as conn:
        return {row[0]: json.loads(row[1]) for row in conn.execute("SELECT product_id,data FROM catalog_edits")}


def edit_product(pid, key, value, actor):
    with transaction(write=True) as conn:
        row = conn.execute("SELECT data FROM catalog_edits WHERE product_id=?", (pid,)).fetchone()
        data = json.loads(row[0]) if row else {}
        old = data.get(key)
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
        conn.execute("INSERT INTO catalog_edits VALUES(?,?) ON CONFLICT(product_id) DO UPDATE SET data=excluded.data",
                     (pid, json.dumps(data, ensure_ascii=False)))
        audit(conn, "catalog", actor, old=str(old), new=str(value), detail=f"{pid} {key}")


def order(oid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM checkout_orders WHERE id=?", (oid,)).fetchone()


def invoice(uid, pid, name, quantity, stars, cost, body, promo=None, review_token=None):
    now = int(time.time())
    token = "tp-" + (review_token or secrets.token_hex(16))
    with transaction(write=True) as conn:
        previous=conn.execute("SELECT * FROM checkout_orders WHERE token=? AND user_id=?",(token,uid)).fetchone()
        if previous:
            return previous
        cur = conn.execute("""INSERT INTO checkout_orders(token,user_id,product_id,name,quantity,stars,
                           quote_usd,body,expires,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                           (token, uid, pid, name, quantity, stars, cost,
                            json.dumps(body) if body is not None else None, now + 600, now, now))
        import promotions
        promotions.reserve(conn,cur.lastrowid,uid,pid,Decimal(cost) if cost else None,promo)
        audit(conn, "invoice", uid, cur.lastrowid, new="invoice")
        return conn.execute("SELECT * FROM checkout_orders WHERE id=?", (cur.lastrowid,)).fetchone()


def find_invoice(token):
    with transaction() as conn:
        return conn.execute("SELECT * FROM checkout_orders WHERE token=?", (token,)).fetchone()


def queue_cv_paid(conn, uid, oid):
    conn.execute("INSERT OR IGNORE INTO push_queue(uid,kind,ref,unique_key) VALUES(?,'cv_paid',?,?)",
                 (uid, str(oid), f"cv_paid:{oid}"))


def approve_invoice(token, uid, stars, currency, query_id):
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM checkout_orders WHERE token=?", (token,)).fetchone()
        if not row or row["user_id"] != uid or row["stars"] != stars or currency != "XTR":
            return False
        if row["state"] != "invoice" or row["expires"] < int(time.time()):
            return False
        if row["approval"] is not None:
            return row["approval"] == query_id
        if row["product_id"] == "cv":
            other = conn.execute("""SELECT id FROM checkout_orders WHERE user_id=? AND product_id='cv'
                AND id!=? AND (state IN ('cv_details','cv_work','refund_requested','refunding','refund_unknown')
                OR (state='invoice' AND approval IS NOT NULL AND expires>=?)) LIMIT 1""",
                                 (uid, row["id"], int(time.time()))).fetchone()
            if other:
                return False
        conn.execute("UPDATE checkout_orders SET approval=? WHERE id=?", (query_id, row["id"]))
        return True


def paid(token, uid, stars, currency, charge):
    now = int(time.time())
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM checkout_orders WHERE token=?", (token,)).fetchone()
        previous = conn.execute("SELECT id FROM checkout_orders WHERE charge=?", (charge,)).fetchone()
        if previous:
            return None
        if not row or row["wallet_amount"] is not None or row["user_id"] != uid or row["stars"] != stars or currency != "XTR" or row["charge"]:
            conn.execute("INSERT OR IGNORE INTO unmatched_payments VALUES(?,?,?,?,?,?)",
                         (charge, uid, token, stars, currency, now))
            if conn.execute("SELECT changes()").fetchone()[0]:
                audit(conn, "unmatched_payment", uid, row["id"] if row else None, detail=charge)
            return None
        state = ("cv_details" if row["product_id"] == "cv" else "paid") if row["approval"] else "review"
        reason = None if row["approval"] else "unapproved_invoice"
        conn.execute("""UPDATE checkout_orders SET state=?,reason=?,charge=?,paid=?,updated=?,due=?
                        WHERE id=?""", (state, reason, charge, now, now, now, row["id"]))
        if state == "cv_details" and row["paid"] is None:
            queue_cv_paid(conn, uid, row["id"])
        audit(conn, "payment", uid, row["id"], row["state"], state)
        return row["id"]


def transition(oid, expected, target, actor=None, action="state", **fields):
    allowed = {"supplier_id", "actual_usd", "delivery", "reason", "due", "delivered", "refunded", "supplier_quantity"}
    if fields.keys() - allowed:
        raise ValueError("Unsupported order field")
    now = int(time.time())
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM checkout_orders WHERE id=?", (oid,)).fetchone()
        if not row or row["state"] not in expected or (target != "invoice" and row["paid"] is None):
            return False
        values = dict(fields, state=target, updated=now)
        columns = ", ".join(f"{key}=?" for key in values)
        conn.execute(f"UPDATE checkout_orders SET {columns} WHERE id=?", (*values.values(), oid))
        if target=="delivered" and row["delivered"] is None:
            import promotions
            promotions.delivered(conn,row)
        if target in ("review","partial","failed"):
            from app_settings import ADMIN_ID
            conn.execute("INSERT OR IGNORE INTO push_queue(uid,kind,ref,unique_key) VALUES(?,'staff',?,?)",
                         (ADMIN_ID,str(oid),f"staff:{oid}:{target}"))
        audit(conn, action, actor, oid, row["state"], target, fields.get("reason"))
        return True


def next_work():
    with transaction() as conn:
        return conn.execute("""SELECT id FROM checkout_orders WHERE state IN ('paid','sending','processing')
                               AND due<=? ORDER BY due,id LIMIT 1""", (int(time.time()),)).fetchone()


def update_refund(charge, uid, stars, currency, token=None):
    if currency != "XTR":
        return False
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM checkout_orders WHERE charge=? AND user_id=? AND stars=?",
                           (charge, uid, stars)).fetchone()
        if not row or (token and row["token"] != token) or row["state"] == "refunded":
            return False
        now = int(time.time())
        conn.execute("UPDATE checkout_orders SET state='refunded',refunded=?,updated=? WHERE id=?",
                     (now, now, row["id"]))
        audit(conn, "refund_confirmed", oid=row["id"], old=row["state"], new="refunded")
        return True


def orders(query="", scope="all", page=0, uid=None, days=0):
    cutoff = int(time.time()) - days * 86400 if days else 0
    by_id = query.lstrip("#")
    references = (by_id, query, query, query)
    indexes = {
        "all": "", "review": " AND state IN ('review','partial','manual','failed')",
        "active": " AND state IN ('paid','sending','processing','review','manual','partial','failed')",
        "refund": " AND state IN ('refund_requested','refunding','refund_unknown')",
        "cv": " AND product_id='cv'",
        "cvopen": " AND product_id='cv' AND state IN ('cv_details','cv_work')", "invoice": " AND state='invoice'",
        "completed": " AND state='delivered'",
    }
    with transaction() as conn:
        return conn.execute("""SELECT * FROM checkout_orders WHERE created>=? AND (? IS NULL OR user_id=?)
            AND (?='' OR CAST(id AS TEXT)=? OR CAST(user_id AS TEXT)=? OR charge=? OR supplier_id=?)
            """ + indexes[scope] + " ORDER BY id DESC LIMIT 9 OFFSET ?",
            (cutoff, uid, uid, query, *references, page * 8)).fetchall()


def metrics(days=0, uid=None):
    cutoff = int(time.time()) - days * 86400 if days else 0
    with transaction() as conn:
        return conn.execute("""SELECT checkout_orders.*, checkout_settlements.usd AS settlement_usd,
             checkout_settlements.reference AS settlement_reference FROM checkout_orders
             LEFT JOIN checkout_settlements ON checkout_settlements.order_id=checkout_orders.id
             WHERE created>=? AND (? IS NULL OR user_id=?) ORDER BY id DESC""", (cutoff, uid, uid)).fetchall()


def settle(oid, amount, reference, actor):
    with transaction(write=True) as conn:
        row = conn.execute("SELECT charge FROM checkout_orders WHERE id=?", (oid,)).fetchone()
        if not row or not row["charge"]:
            return False
        old = conn.execute("SELECT usd,reference FROM checkout_settlements WHERE order_id=?", (oid,)).fetchone()
        conn.execute("""INSERT INTO checkout_settlements VALUES(?,?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET usd=excluded.usd,reference=excluded.reference,recorded=excluded.recorded""",
                     (oid, str(amount), reference, int(time.time())))
        audit(conn, "settlement", actor, oid, " · ".join(old) if old else None, f"{amount} · {reference}")
        return True


def ticket(oid, uid, message, category):
    with transaction(write=True) as conn:
        conn.execute("""INSERT INTO support_tickets(order_id,user_id,message,category,updated)
            VALUES(?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET message=excluded.message,
            category=excluded.category,status='open',updated=excluded.updated""",
            (oid, uid, message, category, int(time.time())))
        conn.execute("INSERT INTO ticket_messages(oid,sender,body,created) VALUES(?,?,?,unixepoch())",(oid,uid,message))
        audit(conn, "support", uid, oid, detail=category)


def get_ticket(oid):
    with transaction() as conn:
        return conn.execute("SELECT * FROM support_tickets WHERE order_id=?", (oid,)).fetchone()


def open_tickets(page):
    with transaction() as conn:
        return conn.execute("SELECT t.* FROM support_tickets t JOIN users u ON u.user_id=t.user_id WHERE status='open'                             ORDER BY (CAST(u.total_spent AS REAL)>=CAST(COALESCE((SELECT value FROM checkout_settings WHERE key='gold_threshold'),'100') AS REAL)) DESC,updated DESC LIMIT 9 OFFSET ?",
                            (page * 8,)).fetchall()


def reply_ticket(oid, actor, message):
    with transaction(write=True) as conn:
        conn.execute("UPDATE support_tickets SET reply=?,status='answered',updated=? WHERE order_id=?",
                     (message, int(time.time()), oid))
        if conn.execute("SELECT changes()").fetchone()[0]:
            conn.execute("INSERT INTO ticket_messages(oid,sender,body,created) VALUES(?,?,?,unixepoch())",(oid,actor,message))
            audit(conn, "support_reply", actor, oid)


def cv_open(uid):
    with transaction() as conn:
        return conn.execute("""SELECT id FROM checkout_orders WHERE user_id=? AND product_id='cv'
           AND state IN ('cv_details','cv_work','refund_requested','refunding','refund_unknown') LIMIT 1""",
                            (uid,)).fetchone()


def wallet_purchase(uid, pid, name, quantity, amount, cost, body, token, syp_rate=None, promo=None):
    from wallet import rate_for

    now = int(time.time())
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM checkout_orders WHERE token=?", (token,)).fetchone()
        if row:
            return row if row["user_id"] == uid else None, False
        if syp_rate != rate_for("SYP", conn=conn):
            return None, False
        account = conn.execute("SELECT balance FROM users WHERE user_id=? AND is_banned=0", (uid,)).fetchone()
        if not account or Decimal(account["balance"]) < amount or not amount.is_finite() or amount <= 0:
            return None, False
        if pid == "cv":
            pending = conn.execute("""SELECT id FROM checkout_orders WHERE user_id=? AND product_id='cv'
                AND (state IN ('cv_details','cv_work','refund_requested','refunding','refund_unknown')
                OR (state='invoice' AND approval IS NOT NULL AND expires>=?)) LIMIT 1""", (uid, now)).fetchone()
            if pending:
                return None, False
        change_balance(conn, uid, -amount)
        state = "cv_details" if pid == "cv" else "paid"
        cur = conn.execute("""INSERT INTO checkout_orders(token,user_id,product_id,name,quantity,stars,quote_usd,
            body,state,expires,created,updated,paid,due,wallet_amount,wallet_syp_rate)
            VALUES(?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?)""",
            (token, uid, pid, name, quantity, cost, json.dumps(body) if body is not None else None,
             state, now, now, now, now, now, str(amount), str(syp_rate) if syp_rate is not None else None))
        import promotions
        promotions.reserve(conn,cur.lastrowid,uid,pid,Decimal(cost) if cost else None,promo)
        if state == "cv_details":
            queue_cv_paid(conn, uid, cur.lastrowid)
        audit(conn, "wallet_purchase", uid, cur.lastrowid, new=state)
        return conn.execute("SELECT * FROM checkout_orders WHERE id=?", (cur.lastrowid,)).fetchone(), True


def refund_wallet(oid, actor):
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM checkout_orders WHERE id=?", (oid,)).fetchone()
        if not row or row["state"] != "refund_requested" or row["wallet_amount"] is None:
            return False
        now = int(time.time())
        change_balance(conn, row["user_id"], Decimal(row["wallet_amount"]))
        conn.execute("UPDATE checkout_orders SET state='refunded',updated=?,refunded=? WHERE id=?", (now, now, oid))
        audit(conn, "wallet_refund", actor, oid, "refund_requested", "refunded")
        return True
