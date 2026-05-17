from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.database import get_db
from backend.auth import get_current_user
from backend.models.class_group import ClassGroup
from backend.models.user import User
from backend.config import settings
from cryptography.fernet import Fernet
from datetime import date, timedelta
import asyncio
import requests
from urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

router = APIRouter(prefix="/api/v1/timetable", tags=["timetable"])

def decrypt_password(enc: str) -> str:
    key = settings.encryption_key
    if not key:
        return enc
    return Fernet(key.encode()).decrypt(enc.encode()).decode()

def _strip_server(url: str) -> str:
    if "://" in url:
        url = url.split("://")[1]
    if "/" in url:
        url = url.split("/")[0]
    return url

def _rpc(session: requests.Session, base: str, school: str, method: str, params: dict, req_id="1"):
    resp = session.post(
        f"https://{base}/WebUntis/jsonrpc.do?school={school}",
        json={"id": req_id, "method": method, "params": params, "jsonrpc": "2.0"},
        verify=False, timeout=15,
    )
    return resp.json()

def _fetch_timetable(server: str, school: str, username: str, password: str,
                     class_name: str, start: date, end: date) -> list:
    start_int = int(start.strftime("%Y%m%d"))
    end_int   = int(end.strftime("%Y%m%d"))

    s = requests.Session()

    # 1. Login
    login = _rpc(s, server, school, "authenticate",
                 {"user": username, "password": password, "client": "sofia"})
    if "error" in login:
        raise Exception("Login fehlgeschlagen: " + login["error"].get("message", ""))
    result     = login["result"]
    session_id = result["sessionId"]
    person_id  = result.get("personId", 0)
    person_type = result.get("personType", 5)
    s.cookies.set("JSESSIONID", session_id)

    try:
        periods = None

        # Strategy 1: own timetable via personId/personType
        if person_id:
            r = _rpc(s, server, school, "getTimetable",
                     {"id": person_id, "type": person_type,
                      "startDate": start_int, "endDate": end_int}, "tt1")
            if not r.get("error") and r.get("result"):
                periods = r["result"]

        # Strategy 2: via class (getKlassen — German method name used by webuntis lib)
        if not periods:
            klassen_resp = _rpc(s, server, school, "getKlassen", {}, "kl")
            klassen = klassen_resp.get("result", [])
            matched = [k for k in klassen if k.get("name", "").lower() == class_name.lower()]
            if not matched and klassen:
                matched = [klassen[0]]
            if matched:
                r = _rpc(s, server, school, "getTimetable",
                         {"id": matched[0]["id"], "type": 1,
                          "startDate": start_int, "endDate": end_int}, "tt2")
                if not r.get("error"):
                    periods = r.get("result", [])

        if periods is None:
            periods = []

        result_list = []
        for p in periods:
            su = p.get("su") or []
            te = p.get("te") or []
            ro = p.get("ro") or []
            code = p.get("code", 0)
            result_list.append({
                "date": str(p.get("date", "")),
                "startTime": p.get("startTime", 0),
                "endTime":   p.get("endTime", 0),
                "subject":       (su[0].get("longName") or su[0].get("name") or "") if su else "",
                "subject_short": su[0].get("name", "") if su else "",
                "teacher":   te[0].get("name", "") if te else "",
                "room":      ro[0].get("name", "") if ro else "",
                "cancelled":   code == 1,
                "substituted": code == 2,
            })
        return sorted(result_list, key=lambda x: (x["date"], x["startTime"]))

    finally:
        try:
            _rpc(s, server, school, "logout", {}, "out")
        except Exception:
            pass

@router.get("/")
async def get_timetable(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not current_user.class_id:
        raise HTTPException(400, "No class assigned")
    result = await db.execute(select(ClassGroup).where(ClassGroup.id == current_user.class_id))
    cls = result.scalar_one_or_none()
    if not cls or not cls.untis_url:
        return {"configured": False}

    try:
        password = decrypt_password(cls.untis_password_enc) if cls.untis_password_enc else ""
        server = _strip_server(cls.untis_url)
        today = date.today()
        this_monday = today - timedelta(days=today.weekday())
        next_monday = this_monday + timedelta(days=7)

        loop = asyncio.get_event_loop()
        this_lessons, next_lessons = await asyncio.gather(
            loop.run_in_executor(None, _fetch_timetable, server, cls.untis_school,
                                 cls.untis_user, password, cls.untis_class or "",
                                 this_monday, this_monday + timedelta(days=4)),
            loop.run_in_executor(None, _fetch_timetable, server, cls.untis_school,
                                 cls.untis_user, password, cls.untis_class or "",
                                 next_monday, next_monday + timedelta(days=4)),
        )

        return {
            "configured": True,
            "this_week": {"start": this_monday.isoformat(), "lessons": this_lessons},
            "next_week": {"start": next_monday.isoformat(), "lessons": next_lessons},
        }
    except Exception as e:
        return {"configured": True, "error": str(e)}
