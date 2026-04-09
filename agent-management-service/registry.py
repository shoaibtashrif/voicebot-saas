import time

active_call_registry = {}

def register_call(call_id: str):
    # Store registration timestamp
    active_call_registry[call_id] = {"registered_at": time.time(), "used": False}
    # Prune calls older than 2 hours to avoid memory leaks
    cutoff = time.time() - 2 * 3600
    for cid in list(active_call_registry.keys()):
        if active_call_registry[cid]["registered_at"] < cutoff:
            del active_call_registry[cid]

def get_most_recent_call_id():
    best = None
    best_time = 0
    for cid, meta in active_call_registry.items():
        if meta["registered_at"] > best_time:
            best_time = meta["registered_at"]
            best = cid
    return best
