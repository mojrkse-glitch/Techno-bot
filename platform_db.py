"""Additive migrations and small, parameterized SQLite helpers."""
from database import transaction


def rows(sql, args=()):
    with transaction() as conn:
        return [dict(row) for row in conn.execute(sql, args)]


def write(sql, args=()):
    with transaction(write=True) as conn:
        return conn.execute(sql, args).rowcount


def init(c):
    tables = {
        "user_sessions": "uid INTEGER PRIMARY KEY, data TEXT, expires_at INTEGER",
        "browse_sort": "uid INTEGER PRIMARY KEY, sorting TEXT",
        "favorites": "uid INTEGER, pid TEXT, PRIMARY KEY(uid,pid)",
        "stock_alerts": "uid INTEGER, pid TEXT, PRIMARY KEY(uid,pid)",
        "design_requests": """id INTEGER PRIMARY KEY,user_id INTEGER,service_type TEXT,status TEXT,brief_text TEXT,
            attachments_json TEXT,admin_note TEXT,created_at INTEGER,updated_at INTEGER,draft_key TEXT UNIQUE""",
        "design_messages": "id INTEGER PRIMARY KEY, request_id INTEGER, sender INTEGER, body TEXT, media TEXT",
        "product_reviews": "oid INTEGER PRIMARY KEY,uid INTEGER,pid TEXT,stars INTEGER CHECK(stars BETWEEN 1 AND 5),comment TEXT",
        "coupons": "code TEXT PRIMARY KEY, kind TEXT, value TEXT, minimum TEXT, cap TEXT, expires INTEGER, max_uses INTEGER, per_user INTEGER, scope TEXT",
        "coupon_redemptions": "oid INTEGER PRIMARY KEY,uid INTEGER,code TEXT",
        "selected_coupon": "uid INTEGER PRIMARY KEY,code TEXT",
        "loyalty_earnings": "oid INTEGER PRIMARY KEY,uid INTEGER,amount TEXT",
        "referral_rewards": "invitee_id INTEGER PRIMARY KEY,inviter INTEGER,oid INTEGER,amount TEXT",
        "push_queue": "id INTEGER PRIMARY KEY,uid INTEGER,kind TEXT,ref TEXT,unique_key TEXT UNIQUE,due INTEGER DEFAULT 0,done INTEGER DEFAULT 0",
        "ticket_messages": "id INTEGER PRIMARY KEY,oid INTEGER,sender INTEGER,body TEXT,created INTEGER",
        "campaigns": "id INTEGER PRIMARY KEY,body TEXT,photo TEXT,link TEXT,creator INTEGER",
        "campaign_targets": "campaign INTEGER,uid INTEGER,status TEXT DEFAULT 'pending',PRIMARY KEY(campaign,uid)",
        "supplier_links": "primary_id TEXT PRIMARY KEY, partner_id TEXT NOT NULL UNIQUE, origin TEXT NOT NULL DEFAULT 'manual', created INTEGER, actor INTEGER",
        "supplier_rejected": "primary_id TEXT, partner_id TEXT, PRIMARY KEY(primary_id, partner_id)",
    }
    for name, fields in tables.items():
        c.execute(f"CREATE TABLE IF NOT EXISTS {name} ({fields})")
    import cv_delivery
    import cv_intake
    cv_intake.init(c)
    cv_delivery.init(c)
    for table, name, definition in [
        ("campaigns","confirmation","TEXT"),("users","is_banned","INTEGER DEFAULT 0"),("users","joined_at","INTEGER"),
        ("checkout_orders","discount_base_stars","INTEGER"),("checkout_orders","sale_usd","TEXT"),
    ]:
        if name not in {r[1] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    c.execute("""CREATE TRIGGER IF NOT EXISTS refund_loyalty AFTER UPDATE OF state ON checkout_orders
        WHEN NEW.state='refunded' AND OLD.state!='refunded' AND EXISTS(SELECT 1 FROM loyalty_earnings WHERE oid=NEW.id)
        BEGIN UPDATE users SET total_spent=printf('%.2f',max(0,round(total_spent*100)-(SELECT round(amount*100) FROM loyalty_earnings WHERE oid=NEW.id))/100.0)
        WHERE user_id=NEW.user_id; DELETE FROM loyalty_earnings WHERE oid=NEW.id; END""")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS campaign_confirmation ON campaigns(confirmation)")
    c.execute("CREATE INDEX IF NOT EXISTS pending_push ON push_queue(done,due,id)")
    c.execute("""CREATE TRIGGER IF NOT EXISTS new_user_date AFTER INSERT ON users BEGIN
        UPDATE users SET joined_at=unixepoch() WHERE user_id=NEW.user_id; END""")
    c.execute("""CREATE TRIGGER IF NOT EXISTS checkout_push AFTER UPDATE OF state ON checkout_orders
        WHEN NEW.state != OLD.state AND NEW.state IN ('partial','delivered','failed','review') BEGIN
        INSERT OR IGNORE INTO push_queue(uid,kind,ref,unique_key) VALUES(NEW.user_id,'order',NEW.id,NEW.id||':'||NEW.state);
        END""")
