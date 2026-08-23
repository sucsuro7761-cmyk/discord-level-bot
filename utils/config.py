import json
import os
import random as _random
from datetime import datetime

import pytz

from .constants import DATA_DIR

_JST = pytz.timezone("Asia/Tokyo")

# =========================
# Config ファイル I/O（メモリキャッシュ付き）
# =========================
_config_cache: dict | None = None

def config_file() -> str:
    return f"{DATA_DIR}/config.json"

def load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    path = config_file()
    if not os.path.exists(path):
        _config_cache = {}
        return _config_cache
    with open(path, "r") as f:
        try:
            _config_cache = json.load(f)
        except json.JSONDecodeError:
            _config_cache = {}
    return _config_cache

def save_config(config: dict) -> None:
    global _config_cache
    _config_cache = config
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(config_file(), "w") as f:
        json.dump(config, f, indent=4)

# =========================
# ショップログ I/O
# =========================
def load_shop_log(guild_id) -> dict:
    path = os.path.join(DATA_DIR, f"{guild_id}_shop_log.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_shop_log(guild_id, log: dict) -> None:
    path = os.path.join(DATA_DIR, f"{guild_id}_shop_log.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

# =========================
# レベル通知チャンネル
# =========================
def get_level_channel_id(guild_id):
    config = load_config()
    return config.get(str(guild_id), {}).get("level_channel_id")

def set_level_channel_id(guild_id, channel_id) -> None:
    config = load_config()
    gid = str(guild_id)
    config.setdefault(gid, {})["level_channel_id"] = channel_id
    save_config(config)

# =========================
# XP獲得チャンネル制限
# =========================
def get_xp_channel_ids(guild_id) -> list:
    config = load_config()
    return config.get(str(guild_id), {}).get("xp_channels", [])

def add_xp_channel_id(guild_id, channel_id) -> None:
    config = load_config()
    gid = str(guild_id)
    config.setdefault(gid, {})
    channels = config[gid].setdefault("xp_channels", [])
    if channel_id not in channels:
        channels.append(channel_id)
    save_config(config)

def remove_xp_channel_id(guild_id, channel_id) -> None:
    config = load_config()
    gid = str(guild_id)
    channels = config.get(gid, {}).get("xp_channels", [])
    if channel_id in channels:
        channels.remove(channel_id)
        save_config(config)

def clear_xp_channels(guild_id) -> None:
    config = load_config()
    gid = str(guild_id)
    if gid in config and "xp_channels" in config[gid]:
        del config[gid]["xp_channels"]
        save_config(config)

# =========================
# 通知ロール
# =========================
def get_notification_role_id(guild_id):
    config = load_config()
    return config.get(str(guild_id), {}).get("notification_role_id")

def set_notification_role_id(guild_id, role_id) -> None:
    config = load_config()
    gid = str(guild_id)
    config.setdefault(gid, {})["notification_role_id"] = role_id
    save_config(config)

def clear_notification_role_id(guild_id) -> None:
    config = load_config()
    gid = str(guild_id)
    if gid in config and "notification_role_id" in config[gid]:
        del config[gid]["notification_role_id"]
        save_config(config)

# =========================
# 称号
# =========================
def _migrate_earned_titles(raw) -> dict:
    """古いリスト形式 → {title_id: stars} dict に変換"""
    if isinstance(raw, dict):
        return raw
    return {tid: 1 for tid in raw} if isinstance(raw, list) else {}

def get_earned_titles(guild_id) -> dict[str, int]:
    """title_id → star_level の辞書を返す（旧リスト形式は自動マイグレーション）"""
    config = load_config()
    gid = str(guild_id)
    raw = config.get(gid, {}).get("earned_titles", {})
    if isinstance(raw, list):
        migrated = _migrate_earned_titles(raw)
        config.setdefault(gid, {})["earned_titles"] = migrated
        save_config(config)
        return migrated
    return raw

def add_earned_title(guild_id, title_id: str, stars: int = 1) -> tuple[bool, int, int]:
    """
    称号を追加または星レベルを更新する。
    Returns: (is_new, old_stars, new_stars)
      is_new   = True if title was not previously earned
      old_stars = star level before this call (0 if is_new)
      new_stars = star level after this call
    """
    config = load_config()
    gid = str(guild_id)
    config.setdefault(gid, {})
    raw = config[gid].get("earned_titles", {})
    titles = _migrate_earned_titles(raw)
    old_stars = titles.get(title_id, 0)
    if stars <= old_stars:
        return False, old_stars, old_stars
    titles[title_id] = stars
    config[gid]["earned_titles"] = titles
    save_config(config)
    return old_stars == 0, old_stars, stars

def set_title_stars(guild_id, title_id: str, stars: int) -> tuple[int, int]:
    """
    称号の星レベルを強制セット（昇格・降格どちらも可）。
    Returns: (old_stars, new_stars)
    """
    config = load_config()
    gid = str(guild_id)
    config.setdefault(gid, {})
    raw = config[gid].get("earned_titles", {})
    titles = _migrate_earned_titles(raw)
    old_stars = titles.get(title_id, 0)
    if old_stars == stars:
        return old_stars, stars
    if stars > 0:
        titles[title_id] = stars
    else:
        titles.pop(title_id, None)
    config[gid]["earned_titles"] = titles
    save_config(config)
    return old_stars, stars

def get_champion_wins(guild_id) -> int:
    config = load_config()
    return config.get(str(guild_id), {}).get("champion_wins", 0)

def increment_champion_wins(guild_id) -> int:
    config = load_config()
    gid = str(guild_id)
    config.setdefault(gid, {})
    wins = config[gid].get("champion_wins", 0) + 1
    config[gid]["champion_wins"] = wins
    save_config(config)
    return wins

def get_active_title(guild_id) -> str | None:
    config = load_config()
    return config.get(str(guild_id), {}).get("active_title")

def set_active_title(guild_id, title_id: str | None) -> None:
    config = load_config()
    gid = str(guild_id)
    config.setdefault(gid, {})["active_title"] = title_id
    save_config(config)

def resolve_display_title(guild_id) -> str | None:
    """表示する称号IDを返す。ランダムモード時は今日の日付でキャッシュして1日1回切り替わる。"""
    active = get_active_title(guild_id)
    if active != "random":
        return active

    config = load_config()
    gid = str(guild_id)
    today = datetime.now(_JST).strftime("%Y-%m-%d")

    cache = config.get(gid, {}).get("random_title_cache", {})
    if cache.get("date") == today and cache.get("title_id"):
        return cache["title_id"]

    earned = get_earned_titles(guild_id)
    if not earned:
        return None

    chosen = _random.choice(list(earned.keys()))
    config.setdefault(gid, {})["random_title_cache"] = {"date": today, "title_id": chosen}
    save_config(config)
    return chosen

def get_display_title_with_stars(guild_id) -> tuple[str | None, int]:
    """(表示称号ID, 星レベル) を返す。未設定なら (None, 0)。"""
    title_id = resolve_display_title(guild_id)
    if not title_id:
        return None, 0
    stars = get_earned_titles(guild_id).get(title_id, 0)
    return title_id, stars
