import json
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

DB_PATH = Path(__file__).with_name("store.db")
FAILED_STATES = {"failed", "refunded", "cancelled"}
PENDING_STATES = {"submitting", "pending", "processing", "under_review"}
CV_PRICE = Decimal("5.00")
CV_OPEN_STATES = ("awaiting_details", "in_progress")


@contextmanager
def transaction(write=False):
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
    finally:
        conn.close()


def init_db():
    with transaction() as c:
        c.execute("PRAGMA journal_mode=WAL")
    with transaction(write=True) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY, lang TEXT DEFAULT 'en',
            balance TEXT DEFAULT '0.00', total_spent TEXT DEFAULT '0.00'
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, jrk_order_id TEXT,
            product_name TEXT, sell_price TEXT, status TEXT, delivery TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        for table, columns in {
            "users": {
                "lang": "TEXT DEFAULT 'en'", "total_spent": "TEXT DEFAULT '0.00'",
                "username": "TEXT DEFAULT ''", "ref_by": "INTEGER",
            },
            "orders": {
                "intent_key": "TEXT", "request_body": "TEXT", "kind": "TEXT NOT NULL DEFAULT 'jrk'",
            },
        }.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        conn.execute("CREATE INDEX IF NOT EXISTS users_ref_by ON users(ref_by)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS orders_intent ON orders(intent_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS orders_customer ON orders(user_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS orders_kind_status ON orders(kind, status, id)")
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS one_open_cv_per_user ON orders(user_id)
            WHERE kind = 'cv' AND status IN ('awaiting_details', 'in_progress')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS shop_settings (
            name TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS product_pricing (
            product_id TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK(mode IN ('percent', 'fixed')),
            amount TEXT NOT NULL
        )""")


def get_user(user_id, username="", ref_by=None, initial_lang="en"):
    with transaction() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row is None or username and row["username"] != username:
        with transaction(write=True) as conn:
            conn.execute("""INSERT OR IGNORE INTO users(user_id, username, lang, balance, ref_by)
                VALUES (?, ?, ?, '0.00', (SELECT user_id FROM users WHERE user_id = ? AND user_id != ?))""",
                (user_id, username, initial_lang, ref_by, user_id))
            if username:
                conn.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    user = dict(row)
    for key in ("balance", "total_spent"):
        user[key] = Decimal(user[key])
    return user


def set_lang(user_id, lang):
    with transaction(write=True) as conn:
        conn.execute("UPDATE users SET lang = ? WHERE user_id = ?", (lang, user_id))


def get_referrals_count(user_id):
    with transaction() as conn:
        return conn.execute("SELECT COUNT(*) FROM users WHERE ref_by = ?", (user_id,)).fetchone()[0]


def change_balance(conn, user_id, amount, spent=Decimal("0.00")):
    row = conn.execute("SELECT balance, total_spent FROM users WHERE user_id = ?", (user_id,)).fetchone()
    balance = (Decimal(row["balance"]) + amount).quantize(Decimal("0.01"))
    total_spent = (Decimal(row["total_spent"]) + spent).quantize(Decimal("0.01"))
    conn.execute("UPDATE users SET balance = ?, total_spent = ? WHERE user_id = ?",
                 (str(balance), str(total_spent), user_id))
    return balance


def update_balance(user_id, amount):
    get_user(user_id)
    with transaction(write=True) as conn:
        return change_balance(conn, user_id, amount)


def reserve_order(user_id, name, price, key, body):
    with transaction(write=True) as conn:
        existing = conn.execute("SELECT * FROM orders WHERE intent_key = ?", (key,)).fetchone()
        if existing:
            return None
        row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if Decimal(row["balance"]) < price:
            return None
        change_balance(conn, user_id, -price)
        cursor = conn.execute("""INSERT INTO orders
            (user_id, product_name, sell_price, status, delivery, intent_key, request_body)
            VALUES (?, ?, ?, 'submitting', '', ?, ?)""",
            (user_id, name, str(price), key, json.dumps(body)))
        return conn.execute("SELECT * FROM orders WHERE id = ?", (cursor.lastrowid,)).fetchone()


def apply_order_result(local_id, order_id, status, delivery, delivered_fraction=Decimal("1")):
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (local_id,)).fetchone()
        if row["status"] not in PENDING_STATES:
            return row
        if row["intent_key"]:
            price = Decimal(row["sell_price"])
            if status in FAILED_STATES:
                change_balance(conn, row["user_id"], price)
            elif status == "completed":
                charged = (price * delivered_fraction).quantize(Decimal("0.01"))
                change_balance(conn, row["user_id"], price - charged, charged)
        conn.execute("""UPDATE orders SET jrk_order_id = ?, status = ?, delivery = ?,
            request_body = CASE WHEN ? IS NULL THEN request_body ELSE NULL END WHERE id = ?""",
            (order_id, status, delivery, order_id, local_id))
        if status in FAILED_STATES:
            conn.execute("UPDATE orders SET request_body = NULL WHERE id = ?", (local_id,))
        return conn.execute("SELECT * FROM orders WHERE id = ?", (local_id,)).fetchone()


def get_user_orders(user_id, limit=10):
    with transaction() as conn:
        return conn.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                            (user_id, limit)).fetchall()


def get_user_stats(user_id):
    with transaction() as conn:
        row = conn.execute("""SELECT COUNT(*), SUM(CASE WHEN delivery != '' THEN 1 ELSE 0 END)
            FROM orders WHERE user_id = ? AND status = 'completed'""", (user_id,)).fetchone()
        return row[0], row[1] or 0


def pricing_settings():
    with transaction() as conn:
        conn.execute("BEGIN")
        global_row = conn.execute("SELECT value FROM shop_settings WHERE name = 'markup'").fetchone()
        overrides = {row["product_id"]: (row["mode"], Decimal(row["amount"]))
                     for row in conn.execute("SELECT * FROM product_pricing")}
    return (Decimal(global_row["value"]) if global_row else None), overrides


def set_markup(percent, actor=None):
    with transaction(write=True) as conn:
        previous = conn.execute("SELECT value FROM shop_settings WHERE name='markup'").fetchone()
        conn.execute("""INSERT INTO shop_settings(name, value) VALUES ('markup', ?)
            ON CONFLICT(name) DO UPDATE SET value = excluded.value""", (str(percent),))
        if actor is not None:
            from commerce_db import audit
            audit(conn, "markup", actor, old=previous[0] if previous else "default", new=str(percent))


def set_product_pricing(product_id, mode, amount=None, actor=None):
    with transaction(write=True) as conn:
        previous = conn.execute("SELECT mode,amount FROM product_pricing WHERE product_id=?", (product_id,)).fetchone()
        if mode == "default":
            conn.execute("DELETE FROM product_pricing WHERE product_id = ?", (product_id,))
        else:
            conn.execute("""INSERT INTO product_pricing(product_id, mode, amount) VALUES (?, ?, ?)
                ON CONFLICT(product_id) DO UPDATE SET mode = excluded.mode, amount = excluded.amount""",
                (product_id, mode, str(amount)))
        if actor is not None:
            from commerce_db import audit
            audit(conn, "product_price", actor, old=" ".join(previous) if previous else "default",
                  new=f"{mode} {amount}" if amount is not None else mode, detail=product_id)


def dashboard_stats():
    with transaction() as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        counts = {row["status"]: row["count"] for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM orders GROUP BY status")}
    return users, counts


def find_users(query, page, page_size=8):
    with transaction() as conn:
        if query.isdecimal():
            if len(query) > 19 or int(query) >= 2**63:
                return []
            return conn.execute("SELECT * FROM users WHERE user_id = ?", (int(query),)).fetchall()
        return conn.execute("""SELECT * FROM users
            WHERE ? = '' OR LOWER(username) = LOWER(?)
            ORDER BY user_id DESC LIMIT ? OFFSET ?""",
            (query, query, page_size + 1, page * page_size)).fetchall()


def admin_user_stats(user_id):
    with transaction() as conn:
        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if user is None:
            return None
        counts = {row["status"]: row["count"] for row in conn.execute(
            "SELECT status, COUNT(*) AS count FROM orders WHERE user_id = ? GROUP BY status", (user_id,))}
        referrals = conn.execute("SELECT COUNT(*) FROM users WHERE ref_by = ?", (user_id,)).fetchone()[0]
        latest = conn.execute("""SELECT id, product_name, status, created_at FROM orders
            WHERE user_id = ? ORDER BY id DESC LIMIT 5""", (user_id,)).fetchall()
        return user, counts, referrals, latest


def create_cv_order(user_id, key, name):
    with transaction(write=True) as conn:
        existing = conn.execute("""SELECT * FROM orders WHERE user_id = ? AND kind = 'cv'
            AND (intent_key = ? OR status IN ('awaiting_details', 'in_progress'))
            ORDER BY (intent_key = ?) DESC LIMIT 1""", (user_id, key, key)).fetchone()
        if existing:
            return existing, False
        account = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if Decimal(account["balance"]) < CV_PRICE:
            return None, False
        change_balance(conn, user_id, -CV_PRICE)
        cursor = conn.execute("""INSERT INTO orders
            (user_id, product_name, sell_price, status, delivery, intent_key, kind)
            VALUES (?, ?, ?, 'awaiting_details', '', ?, 'cv')""",
            (user_id, name, str(CV_PRICE), key))
        return conn.execute("SELECT * FROM orders WHERE id = ?", (cursor.lastrowid,)).fetchone(), True


def cv_orders(page, include_closed=False, page_size=8):
    with transaction() as conn:
        return conn.execute("""SELECT * FROM orders WHERE kind = 'cv'
            AND (? OR status IN ('awaiting_details', 'in_progress'))
            ORDER BY id DESC LIMIT ? OFFSET ?""", (include_closed, page_size + 1, page * page_size)).fetchall()


def cv_order(order_id):
    with transaction() as conn:
        return conn.execute("SELECT * FROM orders WHERE id = ? AND kind = 'cv'", (order_id,)).fetchone()


def change_cv_status(order_id, target):
    with transaction(write=True) as conn:
        row = conn.execute("SELECT * FROM orders WHERE id = ? AND kind = 'cv'", (order_id,)).fetchone()
        if not row or row["status"] not in CV_OPEN_STATES or target == row["status"]:
            return row, False
        if target == "completed":
            change_balance(conn, row["user_id"], Decimal("0"), Decimal(row["sell_price"]))
        elif target == "cancelled":
            change_balance(conn, row["user_id"], Decimal(row["sell_price"]))
        elif target != "in_progress":
            return row, False
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (target, order_id))
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone(), True


def get_open_cv(user_id):
    with transaction() as conn:
        return conn.execute("""SELECT * FROM orders WHERE user_id = ? AND kind = 'cv'
            AND status IN ('awaiting_details', 'in_progress')""", (user_id,)).fetchone()
