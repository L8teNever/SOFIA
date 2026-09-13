import time
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, date

logger = logging.getLogger(__name__)

# Cache: class_id -> {
#   "timestamp": float,
#   "this_week": dict,
#   "next_week": dict,
#   "error": Optional[str]
# }
_timetable_cache: Dict[int, Dict[str, Any]] = {}

CACHE_TTL_SECONDS = 300  # 5 minutes

def get_cached_timetable(class_id: int) -> Optional[Dict[str, Any]]:
    cached = _timetable_cache.get(class_id)
    if not cached:
        return None
    if time.time() - cached.get("timestamp", 0) > CACHE_TTL_SECONDS:
        return None
    return cached

def set_cached_timetable(class_id: int, this_week: dict, next_week: dict, error: Optional[str] = None) -> Dict[str, Any]:
    entry = {
        "timestamp": time.time(),
        "this_week": this_week,
        "next_week": next_week,
        "error": error
    }
    _timetable_cache[class_id] = entry
    return entry

def peek_cached_lessons(class_id: int) -> List[dict]:
    """Returns all lessons (this_week + next_week) currently in cache, regardless of TTL."""
    cached = _timetable_cache.get(class_id)
    if not cached:
        return []
    lessons = []
    if cached.get("this_week") and isinstance(cached["this_week"].get("lessons"), list):
        lessons.extend(cached["this_week"]["lessons"])
    if cached.get("next_week") and isinstance(cached["next_week"].get("lessons"), list):
        lessons.extend(cached["next_week"]["lessons"])
    return lessons

def detect_timetable_changes(old_lessons: List[dict], new_lessons: List[dict]) -> List[dict]:
    """Compares previous lessons against new lessons and finds upcoming cancellations,
    substitutions, or room changes."""
    if not old_lessons or not new_lessons:
        return []

    today_str = datetime.now().strftime("%Y%m%d")
    now_hhmm = int(datetime.now().strftime("%H%M"))

    old_map = {}
    for l in old_lessons:
        # Only track upcoming lessons
        if l["date"] > today_str or (l["date"] == today_str and l["endTime"] >= now_hhmm):
            key = (l["date"], l["startTime"], (l.get("subject_short") or l.get("subject") or ""))
            old_map[key] = l

    changes = []
    for l in new_lessons:
        if l["date"] < today_str or (l["date"] == today_str and l["endTime"] < now_hhmm):
            continue

        key = (l["date"], l["startTime"], (l.get("subject_short") or l.get("subject") or ""))
        old = old_map.get(key)
        if not old:
            continue

        subj = l.get("subject") or l.get("subject_short") or "Unterricht"
        date_formatted = f"{l['date'][6:8]}.{l['date'][4:6]}."
        start_time_str = f"{l['startTime'] // 100:02d}:{l['startTime'] % 100:02d}"

        # 1. Newly cancelled
        if l.get("cancelled") and not old.get("cancelled"):
            changes.append({
                "type": "cancelled",
                "key": f"cancel_{key[0]}_{key[1]}_{key[2]}",
                "title": f"❌ {subj} fällt aus",
                "body": f"Am {date_formatted} um {start_time_str} Uhr",
                "lesson": l,
            })
            continue

        # 2. Newly substituted / irregular
        if l.get("substituted") and not old.get("substituted") and not l.get("cancelled"):
            teacher = l.get("teacher") or "Vertretung"
            room = l.get("room") or ""
            info = f"Lehrer: {teacher}" + (f", Raum: {room}" if room else "")
            changes.append({
                "type": "substituted",
                "key": f"subst_{key[0]}_{key[1]}_{key[2]}_{teacher}_{room}",
                "title": f"🔄 Vertretung: {subj}",
                "body": f"{date_formatted} um {start_time_str} Uhr ({info})",
                "lesson": l,
            })
            continue

        # 3. Room changed
        if l.get("room") and old.get("room") and l["room"] != old["room"] and not l.get("cancelled"):
            changes.append({
                "type": "room_change",
                "key": f"room_{key[0]}_{key[1]}_{key[2]}_{l['room']}",
                "title": f"🚪 Raumänderung: {subj}",
                "body": f"{date_formatted} um {start_time_str} Uhr neu in Raum {l['room']}",
                "lesson": l,
            })

    return changes
