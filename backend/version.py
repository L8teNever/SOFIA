import os, json, subprocess
from datetime import datetime

VERSION = "1.2.0"

def get_git_commit() -> str:
    env_commit = os.getenv("GIT_COMMIT") or os.getenv("GITHUB_SHA")
    if env_commit:
        return env_commit[:7]

    json_path = os.path.join(os.path.dirname(__file__), "version.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("commit"):
                    return data["commit"][:7]
        except Exception:
            pass

    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except Exception:
        return "main"

def get_version_info(build_ts: str) -> dict:
    commit = get_git_commit()
    try:
        ts_int = int(build_ts)
        build_date = datetime.fromtimestamp(ts_int).strftime("%d.%m.%Y, %H:%M")
    except Exception:
        build_date = datetime.now().strftime("%d.%m.%Y, %H:%M")

    return {
        "version": VERSION,
        "commit": commit,
        "build_ts": build_ts,
        "build_date": build_date,
        "channel": "Production",
    }
