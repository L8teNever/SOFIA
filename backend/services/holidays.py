import urllib.request
import json
import logging
from typing import List, Dict, Optional

logger = logging.getLogger("sofia.holidays")

GERMAN_STATES = [
    {"code": "BW", "name": "Baden-Württemberg"},
    {"code": "BY", "name": "Bayern"},
    {"code": "BE", "name": "Berlin"},
    {"code": "BB", "name": "Brandenburg"},
    {"code": "HB", "name": "Bremen"},
    {"code": "HH", "name": "Hamburg"},
    {"code": "HE", "name": "Hessen"},
    {"code": "MV", "name": "Mecklenburg-Vorpommern"},
    {"code": "NI", "name": "Niedersachsen"},
    {"code": "NW", "name": "Nordrhein-Westfalen"},
    {"code": "RP", "name": "Rheinland-Pfalz"},
    {"code": "SL", "name": "Saarland"},
    {"code": "SN", "name": "Sachsen"},
    {"code": "ST", "name": "Sachsen-Anhalt"},
    {"code": "SH", "name": "Schleswig-Holstein"},
    {"code": "TH", "name": "Thüringen"},
]

# Built-in offline fallback data for official KMK school holiday periods
STATIC_KMK_FALLBACK = {
    "NW": {
        2025: [
            {"name": "Osterferien", "start": "2025-04-14", "end": "2025-04-26"},
            {"name": "Pfingstferien", "start": "2025-06-10", "end": "2025-06-10"},
            {"name": "Sommerferien", "start": "2025-07-14", "end": "2025-08-26"},
            {"name": "Herbstferien", "start": "2025-10-13", "end": "2025-10-25"},
            {"name": "Weihnachtsferien", "start": "2025-12-22", "end": "2026-01-06"},
        ],
        2026: [
            {"name": "Osterferien", "start": "2026-03-30", "end": "2026-04-11"},
            {"name": "Pfingstferien", "start": "2026-05-26", "end": "2026-05-26"},
            {"name": "Sommerferien", "start": "2026-07-20", "end": "2026-09-01"},
            {"name": "Herbstferien", "start": "2026-10-17", "end": "2026-10-31"},
            {"name": "Weihnachtsferien", "start": "2026-12-23", "end": "2027-01-06"},
        ],
        2027: [
            {"name": "Osterferien", "start": "2027-03-22", "end": "2027-04-03"},
            {"name": "Pfingstferien", "start": "2027-05-18", "end": "2027-05-18"},
            {"name": "Sommerferien", "start": "2027-07-19", "end": "2027-08-31"},
            {"name": "Herbstferien", "start": "2027-10-23", "end": "2027-11-06"},
            {"name": "Weihnachtsferien", "start": "2027-12-24", "end": "2028-01-08"},
        ],
    },
    "BY": {
        2025: [
            {"name": "Frühjahrsferien", "start": "2025-03-03", "end": "2025-03-07"},
            {"name": "Osterferien", "start": "2025-04-14", "end": "2025-04-25"},
            {"name": "Pfingstferien", "start": "2025-06-10", "end": "2025-06-20"},
            {"name": "Sommerferien", "start": "2025-08-01", "end": "2025-09-15"},
            {"name": "Herbstferien", "start": "2025-11-03", "end": "2025-11-07"},
            {"name": "Weihnachtsferien", "start": "2025-12-22", "end": "2026-01-05"},
        ],
        2026: [
            {"name": "Frühjahrsferien", "start": "2026-02-16", "end": "2026-02-20"},
            {"name": "Osterferien", "start": "2026-03-30", "end": "2026-04-10"},
            {"name": "Pfingstferien", "start": "2026-05-26", "end": "2026-06-05"},
            {"name": "Sommerferien", "start": "2026-08-03", "end": "2026-09-14"},
            {"name": "Herbstferien", "start": "2026-11-02", "end": "2026-11-06"},
            {"name": "Weihnachtsferien", "start": "2026-12-24", "end": "2027-01-08"},
        ],
    }
}

def clean_holiday_name(raw_name: str) -> str:
    if not raw_name:
        return "Ferien"
    n = raw_name.strip()
    nl = n.lower()
    if "oster" in nl:
        return "Osterferien"
    elif "sommer" in nl:
        return "Sommerferien"
    elif "herbst" in nl:
        return "Herbstferien"
    elif "weihnacht" in nl:
        return "Weihnachtsferien"
    elif "winter" in nl or "frühjahr" in nl or "fasching" in nl:
        return "Winterferien"
    elif "pfingst" in nl:
        return "Pfingstferien"
    elif "unterrichtsfrei" in nl or "ferientag" in nl:
        return "Unterrichtsfreier Tag"
    return n.capitalize()

def fetch_holidays(state_code: str, year: int) -> List[Dict]:
    state = state_code.upper().strip()
    results = []

    # 1. Try OpenHolidays API (primary, structured, reliable)
    try:
        url = f"https://openholidaysapi.org/SchoolHolidays?countryIsoCode=DE&subdivisionCode=DE-{state}&validFrom={year}-01-01&validTo={year}-12-31&languageIsoCode=DE"
        req = urllib.request.Request(url, headers={"User-Agent": "Sofia-Schulbegleiter/1.0 (https://sofia.schule)"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for item in data:
                raw_name = ""
                if isinstance(item.get("name"), list) and len(item["name"]) > 0:
                    raw_name = item["name"][0].get("text", "")
                elif isinstance(item.get("name"), str):
                    raw_name = item["name"]
                
                name = clean_holiday_name(raw_name)
                start_date = item.get("startDate", "")[:10]
                end_date = item.get("endDate", "")[:10]
                if start_date and end_date:
                    results.append({
                        "name": name,
                        "start": start_date,
                        "end": end_date,
                        "year": year,
                        "state": state
                    })
    except Exception as e:
        logger.warning(f"OpenHolidays API fetch failed for {state}-{year}: {e}")

    # 2. If empty, try ferien-api.de
    if not results:
        try:
            url = f"https://ferien-api.de/api/v1/holidays/{state}/{year}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data:
                    name = clean_holiday_name(item.get("name", ""))
                    start_date = item.get("start", "")[:10]
                    end_date = item.get("end", "")[:10]
                    if start_date and end_date:
                        results.append({
                            "name": name,
                            "start": start_date,
                            "end": end_date,
                            "year": year,
                            "state": state
                        })
        except Exception as e:
            logger.warning(f"ferien-api.de fetch failed for {state}-{year}: {e}")

    # 3. If still empty, fall back to built-in static KMK data
    if not results and state in STATIC_KMK_FALLBACK and year in STATIC_KMK_FALLBACK[state]:
        for item in STATIC_KMK_FALLBACK[state][year]:
            results.append({
                "name": item["name"],
                "start": item["start"],
                "end": item["end"],
                "year": year,
                "state": state
            })

    # Sort ascending by start date
    results.sort(key=lambda x: x["start"])
    return results
