import json
import time
from decimal import Decimal
from threading import RLock, local
from functools import wraps
from platform_db import rows, write

_locks = [RLock() for _ in range(101)]
_local = local()


def encode(value):
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}
    raise TypeError(type(value).__name__)


class Sessions(dict):
    """Cache one user's working state only for the duration of a handler."""
    def __contains__(self, uid):
        return bool(rows("SELECT 1 FROM user_sessions WHERE uid=? AND expires_at>unixepoch()", (uid,))) or super().__contains__(uid)

    def handler(self, fn):
        @wraps(fn)
        def wrapped(event, *args, **kwargs):
            uid = event.from_user.id
            with _locks[uid % len(_locks)]:
                if getattr(_local, "uid", None) == uid:
                    return fn(event,*args,**kwargs)
                _local.uid = uid
                try:
                    saved = rows("SELECT data FROM user_sessions WHERE uid=? AND expires_at>unixepoch()", (uid,))
                    if saved:
                        self[uid] = json.loads(saved[0]["data"],object_hook=lambda v: Decimal(v["$decimal"]) if set(v)=={"$decimal"} else v)
                    return fn(event,*args,**kwargs)
                finally:
                    state = self.pop(uid,None)
                    try:
                        if state:
                            state.pop("busy",None)
                            write("INSERT OR REPLACE INTO user_sessions VALUES(?,?,?)",(uid,json.dumps(state,default=encode),int(time.time())+86400))
                        else:
                            write("DELETE FROM user_sessions WHERE uid=?", (uid,))
                    finally:
                        _local.uid = None
        return wrapped
