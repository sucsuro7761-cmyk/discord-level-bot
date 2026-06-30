import asyncio
import json
import os
import time
from datetime import datetime

from .constants import DATA_DIR, JST, GLOBAL_EVENT_BOSS_FILE, DAILY_MISSIONS

# =========================
# ファイルパス
# =========================
def data_file(guild_id) -> str:
    return f"{DATA_DIR}/levels_{guild_id}.json"

def boss_file(guild_id) -> str:
    return f"{DATA_DIR}/boss_{guild_id}.json"

def event_boss_file(guild_id) -> str:
    return f"{DATA_DIR}/event_boss_{guild_id}.json"

# =========================
# データ競合防止ロック（ギルドごと）
# =========================
_data_locks: dict[int, asyncio.Lock] = {}

def get_data_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in _data_locks:
        _data_locks[guild_id] = asyncio.Lock()
    return _data_locks[guild_id]

# =========================
# ユーザーデータ I/O
# =========================
def load_data(guild_id) -> dict:
    path = data_file(guild_id)
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_data(guild_id, data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(data_file(guild_id), "w") as f:
        json.dump(data, f, indent=4)

# =========================
# ボスデータ I/O
# =========================
_BOSS_DEFAULT = {"active": False, "hp": 0, "max_hp": 0, "damage": {}, "week": 0, "cleared": 0}

def load_boss(guild_id) -> dict:
    path = boss_file(guild_id)
    if not os.path.exists(path):
        return dict(_BOSS_DEFAULT)
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return dict(_BOSS_DEFAULT)

def save_boss(guild_id, boss: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(boss_file(guild_id), "w") as f:
        json.dump(boss, f, indent=4)

# =========================
# イベントボスデータ I/O
# =========================
_EVENT_BOSS_DEFAULT = {
    "active": False, "hp": 0, "max_hp": 0, "damage": {},
    "name": "大魔王", "consecutive_clears": 0,
}
_GLOBAL_BOSS_DEFAULT = {
    "active": False, "hp": 0, "max_hp": 0, "name": "",
    "damage": {}, "boost_days": 7, "boost_multiplier": 3,
}

def load_global_event_boss() -> dict:
    if os.path.exists(GLOBAL_EVENT_BOSS_FILE):
        with open(GLOBAL_EVENT_BOSS_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass
    return dict(_GLOBAL_BOSS_DEFAULT)

def save_global_event_boss(boss: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(GLOBAL_EVENT_BOSS_FILE, "w", encoding="utf-8") as f:
        json.dump(boss, f, ensure_ascii=False, indent=2)

def load_event_boss(guild_id) -> dict:
    path = event_boss_file(guild_id)
    if not os.path.exists(path):
        return dict(_EVENT_BOSS_DEFAULT)
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return dict(_EVENT_BOSS_DEFAULT)

def save_event_boss(guild_id, boss: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(event_boss_file(guild_id), "w") as f:
        json.dump(boss, f, indent=4)

# =========================
# ユーザーデータ初期化
# =========================
def ensure_user_data(data: dict, user_id: str) -> dict:
    if user_id not in data:
        data[user_id] = {}
    info = data[user_id]
    info.setdefault("xp", 0)
    info.setdefault("level", 1)
    info.setdefault("last_daily", "")
    info.setdefault("weekly_xp", 0)
    info.setdefault("login_streak", 0)
    info.setdefault("weekly_chat_xp", 0)
    info.setdefault("weekly_vc_xp", 0)
    info.setdefault("weekly_active_days", [])
    info.setdefault("last_weekly_xp", 0)
    info.setdefault("last_weekly_rank", 0)
    info.setdefault("coins", 0)
    info.setdefault("buffs", {})
    info.setdefault("coin_daily_earned", 0)
    info.setdefault("coin_total_spent", 0)
    info.setdefault("weekly_coins_earned", 0)
    info.setdefault("weekly_coins_spent", 0)
    info.setdefault("weekly_missions_cleared", 0)
    info.setdefault("weekly_boss_damage", 0)
    info.setdefault("weekly_chest_count", 0)
    info.setdefault("weekly_shop_purchases", 0)
    info.setdefault("last_active_date", "")
    info.setdefault("level_down_streak", 0)
    info.setdefault("decay_warning_days", 0)
    return info

# =========================
# コイン・バフ操作
# =========================
def now_ts() -> int:
    return int(time.time())

def spend_coins(data: dict, user_id: str, amount, reason="spend") -> bool:
    info = ensure_user_data(data, user_id)
    amount = int(amount)
    if amount <= 0 or info.get("coins", 0) < amount:
        return False
    info["coins"] -= amount
    info["coin_total_spent"] = info.get("coin_total_spent", 0) + amount
    return True

def cleanup_expired_buffs(info: dict) -> None:
    current = now_ts()
    buffs = info.setdefault("buffs", {})
    expired = [key for key, buff in buffs.items() if buff.get("expires_at", 0) <= current]
    for key in expired:
        del buffs[key]

def add_timed_buff(info: dict, buff_type: str, value, duration_seconds, item_id: str) -> None:
    cleanup_expired_buffs(info)
    current = now_ts()
    buffs = info.setdefault("buffs", {})
    old = buffs.get(buff_type)
    expires_at = current + int(duration_seconds)
    if old and old.get("expires_at", 0) > current:
        expires_at = max(expires_at, old["expires_at"] + int(duration_seconds))
    buffs[buff_type] = {"value": value, "expires_at": expires_at, "item_id": item_id}

# =========================
# デイリーミッション進捗
# =========================
def get_today_mission() -> dict:
    weekday = datetime.now(JST).weekday()
    return DAILY_MISSIONS[weekday]

def get_mission_progress(info: dict, mission_type: str) -> int:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    progress = info.get("daily_mission_progress", {})
    if progress.get("date") != today:
        return 0
    return progress.get(mission_type, 0)

def add_mission_progress(info: dict, mission_type: str, amount=1) -> None:
    today = datetime.now(JST).strftime("%Y-%m-%d")
    progress = info.get("daily_mission_progress", {})
    if progress.get("date") != today:
        progress = {"date": today}
    progress[mission_type] = progress.get(mission_type, 0) + amount
    info["daily_mission_progress"] = progress
