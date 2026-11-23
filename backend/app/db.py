"""Postgres connection pool. `cursor()` is the only way the app touches the DB."""
import threading
from contextlib import contextmanager

import psycopg2
from psycopg2 import pool as pgpool

from . import config

# Previously every query opened a fresh connection — a full TCP + TLS
# handshake per query, several per request, which exhausts a hosted
# database's connection limit long before the app itself saturates. Pool instead; connections are still returned promptly so none is
# held across a multi-second LLM roundtrip.
_pool = None
_pool_lock = threading.Lock()
# Bounds checkouts to the pool size so callers queue for a connection instead
# of getting PoolError the moment all are in use. (A gevent-friendly lock
# under the gevent worker, which monkey-patches threading.)
_slots = threading.BoundedSemaphore(config.DB_POOL_MAX)


def _get_pool():
    """Built on first use, not at import: gunicorn binds the listen socket
    before workers import this module, so anything slow at import is invisible
    to the platform and surfaces as a 502 against a "healthy" service. A slow
    or unreachable database now degrades individual requests instead.
    Double-checked under a lock: gevent can switch greenlets inside connect()."""
    global _pool
    if _pool is not None or not config.DATABASE_URL:
        return _pool
    with _pool_lock:
        if _pool is None:
            try:
                _pool = pgpool.ThreadedConnectionPool(config.DB_POOL_MIN, config.DB_POOL_MAX, config.DATABASE_URL)
                print(f"DB pool ready ({config.DB_POOL_MIN}-{config.DB_POOL_MAX} connections).")
            except Exception as e:
                print(f"DB pool init failed, falling back to per-request connects: {e}")
    return _pool


@contextmanager
def cursor():
    """Pooled connection in a transaction, committed and returned as soon as
    the block ends. Never hold one across an LLM roundtrip."""
    pool = _get_pool()
    if pool and not _slots.acquire(timeout=config.DB_POOL_WAIT_S):
        raise pgpool.PoolError(f"no database connection free within {config.DB_POOL_WAIT_S}s")
    try:
        conn = pool.getconn() if pool else psycopg2.connect(config.DATABASE_URL)
    except Exception:
        if pool:
            _slots.release()
        raise
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
        cur.close()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        # Return the connection to the pool that produced it — re-reading the
        # global here could hand it to a pool built by another greenlet.
        if pool:
            pool.putconn(conn)
            _slots.release()
        else:
            conn.close()
