"""In-memory pub/sub for live location streaming over WebSockets (single process).
For multi-instance deployments swap for Redis pub/sub."""
import asyncio
import json
from collections import defaultdict

_subs: dict[int, set] = defaultdict(set)  # tenant_id -> {(websocket, allowed_user_ids)}
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop):
    global _loop
    _loop = loop


async def subscribe(tenant_id, ws, allowed: set[int]):
    _subs[tenant_id].add((ws, frozenset(allowed)))


def unsubscribe(tenant_id, ws):
    _subs[tenant_id] = {s for s in _subs[tenant_id] if s[0] is not ws}


async def _send(tenant_id, user_id, payload):
    dead = []
    for ws, allowed in list(_subs[tenant_id]):
        if user_id in allowed:
            try:
                await ws.send_text(json.dumps(payload))
            except Exception:
                dead.append(ws)
    for ws in dead:
        unsubscribe(tenant_id, ws)


def publish(tenant_id, user_id, payload):
    """Safe to call from sync (threadpool) endpoints."""
    if _loop and _subs.get(tenant_id):
        asyncio.run_coroutine_threadsafe(_send(tenant_id, user_id, payload), _loop)
