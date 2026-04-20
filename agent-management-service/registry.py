import time

active_call_registry = {}
cabee_call_registry = {}

def register_call(call_id: str, phone_number: str = None, cabee_call_id: str = None):
    # Store registration timestamp and map by phone
    active_call_registry[call_id] = {
        "phone_number": phone_number,
        "registered_at": time.time(), 
        "used": False,
        "cabee_call_id": cabee_call_id
    }
    
    if cabee_call_id:
        cabee_call_registry[cabee_call_id] = {
            "ultravox_call_id": call_id,
            "registered_at": time.time()
        }
        
    _prune_registries()

def get_call_id_by_cabee(cabee_id: str):
    """Retrieve Ultravox Call ID using the Cabee Call ID."""
    if not cabee_id:
        return None
    entry = cabee_call_registry.get(cabee_id)
    if entry:
        return entry["ultravox_call_id"]
    return None

def _prune_registries():
    # Prune calls older than 2 hours to avoid memory leaks
    cutoff = time.time() - 2 * 3600
    
    for cid in list(active_call_registry.keys()):
        if active_call_registry[cid]["registered_at"] < cutoff:
            del active_call_registry[cid]
            
    for cab_id in list(cabee_call_registry.keys()):
        if cabee_call_registry[cab_id]["registered_at"] < cutoff:
            del cabee_call_registry[cab_id]

def get_most_recent_call_id():
    best = None
    best_time = 0
    for cid, meta in active_call_registry.items():
        if meta["registered_at"] > best_time:
            best_time = meta["registered_at"]
            best = cid
    return best

def get_call_id_by_phone(phone_number: str):
    """Retrieve Call ID exactly matching the normalized phone number feature."""
    if not phone_number:
        return None
    # Strip non-numeric from searching phone number
    search_num = ''.join(filter(str.isdigit, phone_number))
    if not search_num:
        return None
        
    best = None
    best_time = 0
    for cid, meta in active_call_registry.items():
        stored_phone = meta.get("phone_number")
        if stored_phone:
            normalized_stored = ''.join(filter(str.isdigit, stored_phone))
            if search_num in normalized_stored or normalized_stored in search_num:
                if meta["registered_at"] > best_time:
                    best_time = meta["registered_at"]
                    best = cid
    return best
