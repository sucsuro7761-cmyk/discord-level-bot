import discord
from discord.ext import commands, tasks
import json
import os
import time
import random
import math
import asyncio
import io
import csv
import logging
from flask import Flask
from threading import Thread
from datetime import datetime, timezone, timedelta
import pytz

# discord の INFO ログ（RESUMED等）を非表示
logging.getLogger("discord").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)

# =========================
# Config
# =========================
DECAY_PERCENT = 0.05
LAST_DECAY_KEY = "last_decay"
DATA_DIR = "/data"
JST = pytz.timezone("Asia/Tokyo")

# bot管理者ID（環境変数 BOT_ADMIN_IDS にカンマ区切りで設定）
_raw_admin_ids = os.environ.get("BOT_ADMIN_IDS", "1118472855865266246")
BOT_ADMIN_IDS = set(int(i.strip()) for i in _raw_admin_ids.split(",") if i.strip().isdigit())

def is_bot_admin(user_id: int) -> bool:
    return user_id in BOT_ADMIN_IDS
# =========================
# Coin / Shop Config
# =========================
COIN_DAILY_CAP = 1500

SHOP_ITEMS = {
    "xp_small": {
        "name": "XP\u30d6\u30fc\u30b9\u30c8\uff08\u5c0f\uff09",
        "price": 1500,
        "description": "XP\u7372\u5f97\u91cf\u304c1.5\u500d\u306b\u306a\u308a\u307e\u3059\uff0810\u5206\uff09",
        "buff_type": "xp_multiplier",
        "value": 1.5,
        "duration": 10 * 60,
    },
    "xp_medium": {
        "name": "XP\u30d6\u30fc\u30b9\u30c8\uff08\u4e2d\uff09",
        "price": 2000,
        "description": "XP\u7372\u5f97\u91cf\u304c2\u500d\u306b\u306a\u308a\u307e\u3059\uff0810\u5206\uff09",
        "buff_type": "xp_multiplier",
        "value": 2.0,
        "duration": 10 * 60,
    },
    "daily_boost": {
        "name": "\u30c7\u30a4\u30ea\u30fc\u5f37\u5316",
        "price": 1000,
        "description": "\u30c7\u30a4\u30ea\u30fc\u5831\u916c\u304c1.5\u500d\u306b\u306a\u308a\u307e\u3059\uff0824\u6642\u9593\uff09",
        "buff_type": "daily_multiplier",
        "value": 1.5,
        "duration": 24 * 60 * 60,
    },
    "attack_up": {
        "name": "\u653b\u6483\u529b\u30a2\u30c3\u30d7",
        "price": 1000,
        "description": "\u30dc\u30b9\u3078\u306e\u30c0\u30e1\u30fc\u30b8\u304c1.2\u500d\u306b\u306a\u308a\u307e\u3059\uff0860\u5206\uff09",
        "buff_type": "damage_multiplier",
        "value": 1.2,
        "duration": 60 * 60,
    },
    "crit_up": {
        "name": "クリティカル強化",
        "price": 5000,
        "description": "クリティカル発生率がアップ！獲得XPに超高倍率ダメージ（15分）",
        "buff_type": "crit_bonus",
        "value": True,
        "duration": 15 * 60,
    },
    "boss_slayer": {
        "name": "ボス特効",
        "price": 2000,
        "description": "ボスへのダメージが1.3倍になります（15分）",
        "buff_type": "boss_damage_multiplier",
        "value": 1.3,
        "duration": 15 * 60,
    },
    "decay_guard": {
        "name": "🛡️ XP減衰ガード",
        "price": 2500,
        "description": "購入した日のXP自然減衰を無効化します（1日限定）",
        "buff_type": "decay_guard",
        "value": True,
        "duration": 24 * 60 * 60,
    },
    "rankdown_shield": {
        "name": "🔰 ランクダウン防止シールド",
        "price": 5000,
        "description": "次の1回のランクダウンを無効化します（使い切り）",
        "buff_type": "rankdown_shield",
        "value": True,
        "duration": None,
    },
    "royal_pass": {
        "name": "👑 ROYAL PASS",
        "price": 50000,
        "description": "XP+20%・デイリー報酬+50%・特別ロール付与（7日間）",
        "buff_type": "royal_pass",
        "value": True,
        "duration": 7 * 24 * 60 * 60,
    },
    "mystery_box": {
        "name": "🎁 ミステリーボックス",
        "price": 3000,
        "description": "開けると何かが飛び出す！ランダム報酬ボックス（専用コマンド /openbox で使用）",
        "buff_type": None,
        "value": None,
        "duration": None,
    },
    "investment_pack": {
        "name": "📈 投資パック",
        "price": 3000,
        "description": "500〜50,000コインを投資！24時間後に /claiminvest で回収（専用コマンド /invest で購入）",
        "buff_type": None,
        "value": None,
        "duration": None,
    },
}

# =========================
# クリティカルシステム
# =========================
#【テキスト用】バフあり/なしで確率が変わる
# ミニCT: バフあり5% / バフなし2.5%  → ×5
# CT:     バフあり3%  / バフなし1.5%  → ×10
# 超CT:   バフあり0.5%  / バフなし0.25% → ×25
# 超+CT:  バフあり0.1%/ バフなし0.05% → ×50
#
# 【VC用】テキストの半分の確率
# ミニCT: バフあり2.5% / バフなし1.25%  → ×5
# CT:     バフあり1.5%  / バフなし0.75%  → ×10
# 超CT:   バフあり0.25%  / バフなし0.125% → ×25
# 超+CT:  バフあり0.05%/ バフなし0.025% → ×50

CRIT_TABLE_TEXT = [
    # (名前, バフあり確率, バフなし確率, 倍率, 絵文字)
    ("超+CT", 0.001,  0.0005,  50, "💥"),
    ("超CT",  0.005,  0.0025,  25, "⚡"),
    ("CT",    0.03,   0.015,   10, "🔥"),
    ("ミニCT",0.05,   0.025,    5, "✨"),
]

CRIT_TABLE_VC = [
    # テキストの半分の確率
    ("超+CT", 0.0005,  0.00025,  50, "💥"),
    ("超CT",  0.0025,  0.00125,  25, "⚡"),
    ("CT",    0.015,   0.0075,   10, "🔥"),
    ("ミニCT",0.025,   0.0125,    5, "✨"),
]

# 後方互換用（テキストをデフォルトとして残す）
CRIT_TABLE = CRIT_TABLE_TEXT

def calc_crit(base_xp, has_crit_buff, is_vc=False):
    """クリティカル判定を行い (最終XP, クリット名orNone, 倍率) を返す"""
    table = CRIT_TABLE_VC if is_vc else CRIT_TABLE_TEXT
    r = random.random()
    cumulative = 0.0
    for name, prob_buff, prob_normal, multiplier, emoji in table:
        prob = prob_buff if has_crit_buff else prob_normal
        cumulative += prob
        if r < cumulative:
            return int(base_xp * multiplier), f"{emoji} {name}！", multiplier
    return base_xp, None, 1

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

vc_users = {}

# =========================
# スパム対策（レートリミット）
# =========================
# { "guild_id:user_id": [timestamp, timestamp, ...] }
spam_message_times = {}

# 寝落ち管理: { "guild_id:user_id": True } → XP停止フラグ
vc_afk_flags = {}
# 最後にVCでXPを獲得した時刻（90分チェック用）: { "guild_id:user_id": timestamp }
vc_last_xp_time = {}

# =========================
# XP BOOST SYSTEM（サーバーごと・独立管理）
# =========================
# 時間帯ブースト: { guild_id: multiplier }  1=無効
guild_time_boost = {}
# ボス討伐ブースト: { guild_id: multiplier }  1=無効
guild_boss_boost = {}

def get_boost(guild_id):
    """2つのブーストを掛け合わせた最終倍率を返す"""
    time_m = guild_time_boost.get(guild_id, 1)
    boss_m = guild_boss_boost.get(guild_id, 1)
    total = time_m * boss_m
    return {"multiplier": total, "active": total > 1}

def set_time_boost(guild_id, multiplier):
    """時間帯ブーストをセット（1=無効）"""
    guild_time_boost[guild_id] = multiplier

def set_boss_boost(guild_id, multiplier):
    """ボス討伐ブーストをセット（1=無効）"""
    guild_boss_boost[guild_id] = multiplier

# =========================
# 二重実行防止フラグ（サーバーごと）
# =========================
_weekly_announced = {}    # { guild_id: date_str }
_mid_announced_today = {} # { guild_id: date_str }
_boss_spawn_announced = {} # { guild_id: date_str }

# =========================
# 週ボスシステム Config
# =========================
BOSS_BASE_HP = 30000
BOSS_HP_SCALE = 1.2

def find_member_globally(bot, user_id: int):
    """全サーバーからメンバーを検索して返す。見つからなければNone。"""
    for guild in bot.guilds:
        member = guild.get_member(user_id)
        if member:
            return member
    return None

def get_member_name(bot, uid: str) -> str:
    """uidからメンバー名を取得。全サーバーから探し、見つからなければID表示。"""
    member = find_member_globally(bot, int(uid))
    return member.display_name if member else f"ID:{uid}"

def get_boss_clear_role_name(cleared: int) -> str:
    """討伐回数からロール名を生成（LV〇〇ボス討伐者）"""
    return f"⚔️ LV{cleared}ボス討伐者"

# =========================
# イベントボス Config
# =========================
EVENT_BOSS_DEFAULT_HP = 150000    # デフォルトHP（管理者が指定可能）
EVENT_BOSS_CLEAR_ROLE = "👑BOSS VIP"
EVENT_BOSS_CONSECUTIVE_CLEARS = 5  # 累計クリア数で発動
EVENT_BOSS_BOOST_MULTIPLIER = 3    # 討伐後のXP倍率（デフォルト）
EVENT_BOSS_BOOST_DAYS = 7          # ブースト日数（デフォルト）

# イベントボス状態 { guild_id: bool }
event_boss_active = {}

# =========================
# ファイルパス（サーバーごと）
# =========================
def data_file(guild_id):
    return f"{DATA_DIR}/levels_{guild_id}.json"

def boss_file(guild_id):
    return f"{DATA_DIR}/boss_{guild_id}.json"

def event_boss_file(guild_id):
    return f"{DATA_DIR}/event_boss_{guild_id}.json"

def config_file():
    return f"{DATA_DIR}/config.json"

# =========================
# Config read/write（通知チャンネルID保存）
# =========================
def load_config():
    path = config_file()
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_config(config):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(config_file(), "w") as f:
        json.dump(config, f, indent=4)

def load_shop_log(guild_id):
    path = os.path.join(DATA_DIR, f"{guild_id}_shop_log.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_shop_log(guild_id, log):
    path = os.path.join(DATA_DIR, f"{guild_id}_shop_log.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)

def get_level_channel_id(guild_id):
    config = load_config()
    return config.get(str(guild_id), {}).get("level_channel_id")

def set_level_channel_id(guild_id, channel_id):
    config = load_config()
    gid = str(guild_id)
    if gid not in config:
        config[gid] = {}
    config[gid]["level_channel_id"] = channel_id
    save_config(config)

def get_xp_channel_ids(guild_id):
    """XPを獲得できるチャンネルIDリストを返す（空=全チャンネル許可）"""
    config = load_config()
    return config.get(str(guild_id), {}).get("xp_channels", [])

def add_xp_channel_id(guild_id, channel_id):
    config = load_config()
    gid = str(guild_id)
    if gid not in config:
        config[gid] = {}
    channels = config[gid].setdefault("xp_channels", [])
    if channel_id not in channels:
        channels.append(channel_id)
    save_config(config)

def remove_xp_channel_id(guild_id, channel_id):
    config = load_config()
    gid = str(guild_id)
    channels = config.get(gid, {}).get("xp_channels", [])
    if channel_id in channels:
        channels.remove(channel_id)
        config[gid]["xp_channels"] = channels
        save_config(config)
        return True
    return False

def clear_xp_channels(guild_id):
    config = load_config()
    gid = str(guild_id)
    if gid in config:
        config[gid]["xp_channels"] = []
        save_config(config)

# =========================
# Data read/write（サーバーごと）
# =========================
def load_data(guild_id):
    path = data_file(guild_id)
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_data(guild_id, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(data_file(guild_id), "w") as f:
        json.dump(data, f, indent=4)
# =========================
# Coin / Buff helpers
# =========================
def now_ts():
    return int(time.time())

def ensure_user_data(data, user_id):
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
    info.setdefault("decay_warning_days", 0)  # 維持条件を下回り続けた日数
    return info

def spend_coins(data, user_id, amount, reason="spend"):
    info = ensure_user_data(data, user_id)
    amount = int(amount)
    if amount <= 0 or info.get("coins", 0) < amount:
        return False

    info["coins"] -= amount
    info["coin_total_spent"] = info.get("coin_total_spent", 0) + amount
    return True

def cleanup_expired_buffs(info):
    current = now_ts()
    buffs = info.setdefault("buffs", {})
    expired = [key for key, buff in buffs.items() if buff.get("expires_at", 0) <= current]
    for key in expired:
        del buffs[key]

def add_timed_buff(info, buff_type, value, duration_seconds, item_id):
    cleanup_expired_buffs(info)
    current = now_ts()
    buffs = info.setdefault("buffs", {})
    old = buffs.get(buff_type)
    expires_at = current + int(duration_seconds)
    if old and old.get("expires_at", 0) > current:
        expires_at = max(old["expires_at"], current) + int(duration_seconds)
    buffs[buff_type] = {
        "value": value,
        "expires_at": expires_at,
        "item_id": item_id,
    }

# =========================
# Boss read/write（サーバーごと）
# =========================
def load_boss(guild_id):
    path = boss_file(guild_id)
    if not os.path.exists(path):
        return {"active": False, "hp": 0, "max_hp": 0, "damage": {}, "week": 0, "cleared": 0}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"active": False, "hp": 0, "max_hp": 0, "damage": {}, "week": 0, "cleared": 0}

def save_boss(guild_id, boss):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(boss_file(guild_id), "w") as f:
        json.dump(boss, f, indent=4)

# =========================
# Flask keep alive
# =========================
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run():
    app.run(host='0.0.0.0', port=5000)

def keep_alive():
    t = Thread(target=run)
    t.start()

# =========================
# Rank definitions
# =========================
rank_roles = [
    (1, 9, "MEMBER Lite"),
    (10, 29, "MEMBER"),
    (30, 49, "CORE"),
    (50, 74, "SELECT"),
    (75, 99, "PREMIUM"),
    (100, 199, "VIP Lite"),
    (200, 499, "VIP"),
    (500, 9999, "Legend")
]

permanent_roles = {
    3: "PHOTO+"
}

weekly_roles = {
    1: "🥇週間王者",
    2: "🥈週間準王",
    3: "🥉週間三位"
}

# =========================
# Rank Role Updater
# =========================
async def update_rank_role(member, level):
    guild = member.guild

    # 現在のランクロールを取得
    current_rank_role = None
    for role in member.roles:
        for _, _, r_name in rank_roles:
            if role.name == r_name:
                current_rank_role = role
                break

    # 目標ランクロールを取得（なければ自動作成）
    target_role = None
    for min_lv, max_lv, role_name in rank_roles:
        if min_lv <= level <= max_lv:
            target_role = discord.utils.get(guild.roles, name=role_name)
            if not target_role:
                try:
                    target_role = await guild.create_role(
                        name=role_name,
                        reason="ランクロール自動作成"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    target_role = None
            break

    if current_rank_role == target_role:
        return False  # ランク変更なし
    try:
        if current_rank_role:
            await member.remove_roles(current_rank_role)
        if target_role:
            await member.add_roles(target_role)
        return True  # ランク変更あり
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"[RANK ERROR] サーバー：{member.guild.name} | ユーザー：{member.display_name} | Lv{level} | {current_rank_role} → {target_role} | エラー：{e}")
        return False

# =========================
# Level-up check
# =========================
async def check_level_up(member, data, user_id):
    guild = member.guild
    ch_id = get_level_channel_id(guild.id)
    notify_channel = guild.get_channel(ch_id) if ch_id else None

    while True:
        current_xp = data[user_id]["xp"]
        current_level = data[user_id]["level"]
        required_xp = current_level * 100

        if current_xp < required_xp:
            break

        data[user_id]["xp"] -= required_xp
        data[user_id]["level"] += 1
        new_level = data[user_id]["level"]

        rank_changed = await update_rank_role(member, new_level)

        # レベルアップコイン付与（レベルに応じて増加、最大500）
        coin_reward = min(100 + (new_level * 10), 500)
        info = ensure_user_data(data, user_id)
        info["coins"] = info.get("coins", 0) + coin_reward
        info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + coin_reward

        # ランクロールが昇格したときのみ通知
        if rank_changed and notify_channel:
            new_rank = None
            for min_lv, max_lv, role_name in rank_roles:
                if min_lv <= new_level <= max_lv:
                    new_rank = role_name
                    break
            try:
                await notify_channel.send(
                    f"🎉 {member.mention} が **{new_rank}** にランクアップしました！ （Lv{new_level}）💰 +{coin_reward}コイン"
                )
            except discord.DiscordServerError:
                pass

        if new_level in permanent_roles:
            role_name = permanent_roles[new_level]
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                try:
                    await member.add_roles(role)
                    if notify_channel:
                        await notify_channel.send(f"📸 {member.mention} が **{role_name}** を獲得しました！")
                except (discord.Forbidden, discord.HTTPException):
                    pass

async def check_level_down(guild, data, uid):
    """
    維持条件：現在レベル × 100 × 10% = 現在レベル × 10 以上のXPを保有していること
    （例：Lv100 → 100×100×10% = 1,000XP以上が必要）
    条件未満が7日間連続で続いた場合のみ1レベルダウン。
    Lv1以下には落ちない。
    """
    info = data.get(uid)
    if not info or not isinstance(info, dict):
        return

    current_level = info.get("level", 1)
    current_xp    = info.get("xp", 0)

    if current_level <= 1:
        info["level_down_streak"] = 0
        return

    # 維持に必要なXP = 現在レベル × 100 × 10%
    required_xp = int(current_level * 100 * 0.1)

    if current_xp >= required_xp:
        # 条件を満たしているのでストリークをリセット
        info["level_down_streak"] = 0
        return

    # 条件未満：連続日数をカウント
    streak = info.get("level_down_streak", 0) + 1
    info["level_down_streak"] = streak

    ch_id = get_level_channel_id(guild.id)
    notify_channel = guild.get_channel(ch_id) if ch_id else None
    member = guild.get_member(int(uid))

    if streak < 7:
        # 毎日警告通知（残り日数をお知らせ）
        remaining = 7 - streak
        if notify_channel and member:
            try:
                await notify_channel.send(
                    f"⚠️ {member.mention} の保有XPが維持条件（**{required_xp:,}XP**）を下回っています。"
                    f"あと **{remaining}日** 続くとランクダウンします！（現在：{current_xp:,}XP）"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        return

    # 7日連続で条件未満 → ランクダウン防止シールドチェック
    if info.get("buffs", {}).get("rankdown_shield"):
        # シールドを消費してランクダウンをキャンセル
        info["buffs"].pop("rankdown_shield", None)
        info["level_down_streak"] = 0
        if notify_channel and member:
            try:
                await notify_channel.send(
                    f"🔰 {member.mention} のランクダウン防止シールドが発動！ランクダウンを1回防ぎました。"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
        return

    # 7日連続で条件未満 → 1レベルダウン
    new_level = max(1, current_level - 1)
    info["level"] = new_level
    info["level_down_streak"] = 0  # ストリークリセット

    if member:
        await update_rank_role(member, new_level)

    if notify_channel and member:
        try:
            await notify_channel.send(
                f"📉 {member.mention} の保有XPが**{required_xp:,}XP**を7日間下回り続けたため、"
                f"**Lv{current_level} → Lv{new_level}** にランクダウンしました。"
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

async def give_streak_mystery_box(member, guild, channel, streak):
    """7の倍数連続ログイン時にミステリーボックスを自動開封して結果を送信"""
    guild_id = str(guild.id)
    user_id  = str(member.id)
    data     = load_data(guild.id)
    info     = ensure_user_data(data, user_id)

    rarity, reward = draw_mystery_box()

    LOSE_PATTERNS = {
        "small_coins": ("空箱だった…\n\nせめてもの慰めに 💰 **+50コイン** をどうぞ。",    50),
        "big_lose":    ("完全な空箱だった…！\n\n慰めに 💰 **+100コイン** をどうぞ。",    100),
        "curse":       ("💀 **呪いのボックス！！**\n\nXPが **-50** 削られてしまった…！", 0),
        "msg1":        ("ゴロゴロ…カラン…\n\n**何も入っていなかった。** 💰 **+100コイン**", 100),
        "msg2":        ("箱を開けたら説明書だけ入ってた。\n\n💰 **+100コイン** で許して。", 100),
    }

    if rarity == "lose":
        msg, coin_back = LOSE_PATTERNS[reward]
        if reward == "curse":
            info["xp"] = max(0, info.get("xp", 0) - 50)
        else:
            info["coins"] = info.get("coins", 0) + coin_back
            info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + coin_back
        save_data(guild.id, data)
        embed = discord.Embed(
            title=f"🎁 {streak}日連続ログイン！ミステリーボックスプレゼント！",
            description=f"{member.mention}\n\n{msg}",
            color=discord.Color.greyple()
        )
        embed.set_footer(text=f"🔥 {streak}日連続ログインおめでとう！")
    else:
        embed_color = discord.Color.gold() if rarity == "normal" else discord.Color.from_rgb(255, 50, 200)
        reward_text = ""

        if reward["type"] == "coins":
            amount = reward["amount"]
            info["coins"] = info.get("coins", 0) + amount
            info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + amount
            reward_text = f"💰 **+{amount:,}コイン** 獲得！"
        elif reward["type"] == "login_bonus_2x":
            info["login_bonus_2x"] = True
            reward_text = "🎁 **翌日のログインボーナス2倍チケット** 獲得！"
        elif reward["type"] == "xp_boost":
            add_timed_buff(info, "xp_multiplier", reward["value"], reward["duration"], "mystery_xp_boost")
            reward_text = f"⚡ **XPブースト {reward['value']}倍**（30分）獲得！"
        elif reward["type"] == "attack_up":
            add_timed_buff(info, "damage_multiplier", reward["value"], reward["duration"], "mystery_attack")
            reward_text = f"⚔️ **攻撃力アップ {reward['value']}倍**（15分）獲得！"
        elif reward["type"] == "boss_slayer":
            add_timed_buff(info, "boss_damage_multiplier", reward["value"], reward["duration"], "mystery_boss")
            reward_text = f"🗡️ **ボス特効 {reward['value']}倍**（30分）獲得！"
        elif reward["type"] == "rare_role":
            role_name = "🎁 ミステリー当選者"
            role = discord.utils.get(guild.roles, name=role_name)
            if not role:
                try:
                    role = await guild.create_role(
                        name=role_name,
                        color=discord.Color.from_rgb(255, 215, 0),
                        reason="ミステリーボックス レア称号"
                    )
                except discord.Forbidden:
                    role = None
            if role:
                try:
                    await member.add_roles(role)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            reward_text = f"👑 **レア称号「{role_name}」** を獲得！"

        save_data(guild.id, data)

        title  = f"🌟✨ {streak}日連続！レア報酬！！ ✨🌟" if rarity == "rare" else f"🎁 {streak}日連続ログイン！ミステリーボックスプレゼント！"
        prefix = f"🎊 {member.mention} おめでとうございます！！\n\n" if rarity == "rare" else f"{member.mention}\n\n"
        embed  = discord.Embed(title=title, description=f"{prefix}{reward_text}", color=embed_color)
        embed.set_footer(text=f"🔥 {streak}日連続ログインおめでとう！")

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass

# =========================
# =========================
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.guild is None:
        return

    guild_id = message.guild.id
    user_id = str(message.author.id)
    current_time = time.time()

    ck = f"{guild_id}:{user_id}"

    # =========================
    # スパムガード（XPブロック）
    # 10秒以内に3メッセージ以上でXP無効
    # =========================
    times = spam_message_times.setdefault(ck, [])
    times.append(current_time)
    spam_message_times[ck] = [t for t in times if current_time - t <= 10]
    xp_blocked = len(spam_message_times[ck]) >= 3

    # XP獲得チャンネル制限チェック（設定済みの場合のみ対象チャンネルでXP付与）
    xp_channels = get_xp_channel_ids(guild_id)
    if xp_channels and message.channel.id not in xp_channels:
        await bot.process_commands(message)
        return

    if xp_blocked:
        await bot.process_commands(message)
        return

    data = load_data(guild_id)
    if user_id not in data:
        data[user_id] = {}

    data[user_id].setdefault("xp", 0)
    data[user_id].setdefault("level", 1)
    data[user_id].setdefault("last_daily", "")
    data[user_id].setdefault("weekly_xp", 0)
    data[user_id].setdefault("login_streak", 0)
    data[user_id].setdefault("weekly_chat_xp", 0)
    data[user_id].setdefault("weekly_vc_xp", 0)
    data[user_id].setdefault("weekly_active_days", [])
    data[user_id].setdefault("last_weekly_xp", 0)
    data[user_id].setdefault("last_weekly_rank", 0)
    data.setdefault(LAST_DECAY_KEY, "")

    # アクティブ日数を記録
    today_jst = datetime.now(JST).strftime("%Y-%m-%d")
    if today_jst not in data[user_id]["weekly_active_days"]:
        data[user_id]["weekly_active_days"].append(today_jst)

    # 最終アクティブ日を記録（減衰判定用）
    data[user_id]["last_active_date"] = today_jst

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    if data[user_id]["last_daily"] != today:
        if data[user_id]["last_daily"] == yesterday:
            data[user_id]["login_streak"] += 1
        else:
            data[user_id]["login_streak"] = 1

        streak = data[user_id]["login_streak"]

        if streak == 1:
            bonus = 100
        elif streak == 2:
            bonus = 200
        elif streak == 3:
            bonus = 300
        elif streak == 4:
            bonus = 500
        else:
            bonus = 1000

        data[user_id]["xp"] += bonus
        data[user_id]["weekly_xp"] += bonus
        data[user_id]["last_daily"] = today

        # ログインボーナスでボスにダメージ
        boss = load_boss(guild_id)
        if boss.get("active"):
            boss["damage"][user_id] = boss["damage"].get(user_id, 0) + bonus
            boss["hp"] = max(0, boss["hp"] - bonus)
            if boss["hp"] <= 0:
                boss["active"] = False
                boss["cleared"] += 1
                save_boss(guild_id, boss)
                await handle_boss_clear(message.guild, boss)
            else:
                save_boss(guild_id, boss)

        # ストリークボーナスコイン（100 + streak * 20、上限500）
        streak_coins = min(100 + (streak * 20), 500)
        info = ensure_user_data(data, user_id)

        # ログインボーナス2倍チケット適用
        if info.get("login_bonus_2x"):
            streak_coins *= 2
            info["login_bonus_2x"] = False

        today_earned = info.get("coin_daily_earned", 0)
        if today_earned < COIN_DAILY_CAP:
            add_amount = min(streak_coins, COIN_DAILY_CAP - today_earned)
            info["coins"] = info.get("coins", 0) + add_amount
            info["coin_daily_earned"] = today_earned + add_amount
            info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + add_amount
        else:
            streak_coins = 0

        if streak == 1:
            streak_msg = "🎁 **デイリーボーナス！**"
        elif streak < 5:
            streak_msg = f"🔥 **{streak}日連続ログイン！**"
        else:
            streak_msg = f"🌟 **{streak}日連続ログイン！MAX ボーナス！**"

        coin_msg = f" 💰 +{streak_coins}コイン" if streak_coins > 0 else ""
        await message.channel.send(
            f"{streak_msg}\n"
            f"{message.author.mention} **+{bonus}XP**{coin_msg} "
            f"（連続{streak}日目）"
        )

        # 7の倍数連続ログインでミステリーボックスプレゼント
        if streak % 7 == 0:
            await give_streak_mystery_box(message.author, message.guild, message.channel, streak)

    boost = get_boost(guild_id)

    # ショップバフ適用（xp_multiplier・crit_bonus）
    data_tmp = load_data(guild_id)
    info_tmp = data_tmp.get(user_id, {})
    cleanup_expired_buffs(info_tmp)
    xp_buff = info_tmp.get("buffs", {}).get("xp_multiplier", {})
    shop_xp_multi = xp_buff.get("value", 1.0) if xp_buff else 1.0
    has_crit_buff = bool(info_tmp.get("buffs", {}).get("crit_bonus"))

    base_xp = int(random.randint(5, 20) * boost["multiplier"] * shop_xp_multi)
    xp_gain, crit_name, crit_multi = calc_crit(base_xp, has_crit_buff)
    data[user_id]["xp"] += xp_gain
    data[user_id]["weekly_xp"] += xp_gain
    data[user_id]["weekly_chat_xp"] = data[user_id].get("weekly_chat_xp", 0) + xp_gain

    # ミッション進捗：メッセージ数・XP獲得
    info = ensure_user_data(data, user_id)
    add_mission_progress(info, "msg_count", 1)
    add_mission_progress(info, "xp_gained", xp_gain)

    # クリティカル発生時に通知
    if crit_name:
        try:
            await message.channel.send(
                f"{crit_name} {message.author.display_name} **+{xp_gain:,}XP**（{crit_multi}倍！）"
            )
        except discord.DiscordServerError:
            pass

    await check_level_up(message.author, data, user_id)

    # 時間帯別アクティブ記録（/peakhours用）
    hour_key = f"hour_{datetime.now(JST).hour}"
    data[user_id][hour_key] = data[user_id].get(hour_key, 0) + 1

    save_data(guild_id, data)

    boss = load_boss(guild_id)
    if boss.get("active"):
        # ボスダメージバフ適用
        boss_dmg_buff = info_tmp.get("buffs", {}).get("boss_damage_multiplier", {})
        boss_multi = boss_dmg_buff.get("value", 1.0) if boss_dmg_buff else 1.0
        actual_dmg = int(xp_gain * boss_multi)
        boss["damage"][user_id] = boss["damage"].get(user_id, 0) + actual_dmg
        boss["hp"] = max(0, boss["hp"] - actual_dmg)

        # ミッション進捗：ボスダメージ
        add_mission_progress(ensure_user_data(data, user_id), "boss_damage", actual_dmg)

        if boss["hp"] <= 0:
            boss["active"] = False
            boss["cleared"] += 1
            save_boss(guild_id, boss)
            await handle_boss_clear(message.guild, boss)
        else:
            save_boss(guild_id, boss)

    # イベントボスへのダメージ
    event_boss = load_event_boss(guild_id)
    if event_boss.get("active"):
        event_boss["damage"][user_id] = event_boss["damage"].get(user_id, 0) + xp_gain
        event_boss["hp"] = max(0, event_boss["hp"] - xp_gain)
        if event_boss["hp"] <= 0:
            event_boss["active"] = False
            save_event_boss(guild_id, event_boss)
            await handle_event_boss_clear(message.guild, event_boss)
        else:
            save_event_boss(guild_id, event_boss)

    # サーバーリンクボスへのダメージ
    global_boss = load_global_event_boss()
    if global_boss.get("active"):
        global_boss["damage"][user_id] = global_boss["damage"].get(user_id, 0) + xp_gain
        global_boss["hp"] = max(0, global_boss["hp"] - xp_gain)
        if global_boss["hp"] <= 0:
            global_boss["active"] = False
            save_global_event_boss(global_boss)
            await handle_global_event_boss_clear(global_boss)
        else:
            save_global_event_boss(global_boss)

    await bot.process_commands(message)

# =========================
# VC XP
# =========================

# =========================
# 寝落ちチェックシステム
# =========================

class AfkCheckView(discord.ui.View):
    def __init__(self, ck, member_id: int):
        super().__init__(timeout=300)  # 5分
        self.ck = ck
        self.member_id = member_id
        self.responded = False

    @discord.ui.button(label="✅ 活動中！", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member_id:
            await interaction.response.send_message(
                "❌ このボタンはあなた宛ではありません。",
                ephemeral=True
            )
            return
        self.responded = True
        vc_afk_flags.pop(self.ck, None)
        await interaction.response.edit_message(
            content="✅ 確認できました！VC XPを継続します。",
            view=None
        )
        self.stop()

async def run_afk_check(member, guild_id, user_id, ck):
    """AFKチェックを実行。タイムアウトしたらTrueを返す（XP停止）"""
    ch_id = get_level_channel_id(guild_id)
    notify_ch = member.guild.get_channel(ch_id) if ch_id else None
    if not notify_ch:
        return False

    view = AfkCheckView(ck, member.id)
    try:
        msg = await notify_ch.send(
            f"⚠️ **寝落ちチェック！** {member.mention}\n"
            f"VCに参加中ですが、活動していますか？\n"
            f"**5分以内にボタンを押さないとXP獲得が停止します！**",
            view=view
        )
    except Exception:
        return False

    await view.wait()  # 5分待つ

    if not view.responded:
        # タイムアウト → AFK確定
        vc_afk_flags[ck] = True
        try:
            await msg.edit(
                content=(
                    f"😴 **{member.display_name}** の応答がなかったためXP獲得を停止しました。\n"
                    f"戻ってきたら `/return` で復帰できます！"
                ),
                view=None
            )
        except Exception:
            pass
        return True

    return False

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    guild_id = member.guild.id
    user_id = str(member.id)
    ck = f"{guild_id}:{user_id}"

    if after.channel and not before.channel:
        vc_users[ck] = True
        vc_last_xp_time[ck] = time.time()
        vc_xp_elapsed = 0  # 最後のチェックからの経過秒数カウント

        # VC入室時のログインボーナス
        data = load_data(guild_id)
        if user_id not in data:
            data[user_id] = {}
        info = ensure_user_data(data, user_id)

        today_jst = datetime.now(JST).strftime("%Y-%m-%d")
        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        yesterday_utc = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        # last_active_date を更新（減衰判定用）
        info["last_active_date"] = today_jst

        if info["last_daily"] != today_utc:
            if info["last_daily"] == yesterday_utc:
                info["login_streak"] += 1
            else:
                info["login_streak"] = 1

            streak = info["login_streak"]
            if streak == 1:
                bonus = 100
            elif streak == 2:
                bonus = 200
            elif streak == 3:
                bonus = 300
            elif streak == 4:
                bonus = 500
            else:
                bonus = 1000

            # ログインボーナス2倍チケット適用
            if info.get("login_bonus_2x"):
                bonus *= 2
                info["login_bonus_2x"] = False

            info["xp"] = info.get("xp", 0) + bonus
            info["weekly_xp"] = info.get("weekly_xp", 0) + bonus
            info["last_daily"] = today_utc
            data[user_id] = info
            save_data(guild_id, data)

            ch_id = get_level_channel_id(guild_id)
            notify_channel = member.guild.get_channel(ch_id) if ch_id else None
            if notify_channel:
                try:
                    await notify_channel.send(
                        f"🎙️ {member.mention} がVCに入室！ログインボーナス **+{bonus}XP**（🔥{streak}日連続）"
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass

            # 7の倍数連続ログインでミステリーボックスプレゼント
            if streak % 7 == 0 and notify_channel:
                await give_streak_mystery_box(member, member.guild, notify_channel, streak)

        while vc_users.get(ck):
            await asyncio.sleep(30)
            vc_xp_elapsed += 30

            if not member.voice or not member.voice.channel:
                break
            if len(member.voice.channel.members) < 2:
                continue

            # AFKフラグが立っていたらXP停止
            if vc_afk_flags.get(ck):
                continue

            # 45分（2700秒）ごとにAFKチェック
            if vc_xp_elapsed >= 2700:
                vc_xp_elapsed = 0
                afk_confirmed = await run_afk_check(member, guild_id, user_id, ck)
                if afk_confirmed:
                    continue  # フラグON → XP停止

            data = load_data(guild_id)
            if user_id not in data:
                data[user_id] = {}
            data[user_id].setdefault("xp", 0)
            data[user_id].setdefault("level", 1)
            data[user_id].setdefault("last_daily", "")
            data[user_id].setdefault("weekly_xp", 0)

            if not member.voice or not member.voice.channel:
                break

            boost = get_boost(guild_id)
            # ミュート中は2XP、ミュート解除（発言中）は15XP
            is_muted = member.voice.self_mute or member.voice.mute if member.voice else True
            base_xp_vc = 2 if is_muted else 15

            # VCクリティカル判定（テキストの半分の確率）
            vc_info = data.get(user_id, {})
            cleanup_expired_buffs(vc_info)
            has_crit_buff_vc = bool(vc_info.get("buffs", {}).get("crit_bonus"))
            base_xp_boosted = int(base_xp_vc * boost["multiplier"])
            gain, crit_name_vc, crit_multi_vc = calc_crit(base_xp_boosted, has_crit_buff_vc, is_vc=True)

            data[user_id]["xp"] += gain
            data[user_id]["weekly_xp"] += gain
            data[user_id]["weekly_vc_xp"] = data[user_id].get("weekly_vc_xp", 0) + gain

            # ミッション進捗：VC滞在（30秒ごとに0.5分加算）・XP獲得
            vc_info_m = ensure_user_data(data, user_id)
            add_mission_progress(vc_info_m, "vc_minutes", 0.5)
            add_mission_progress(vc_info_m, "xp_gained", gain)

            # VCクリティカル発生時に通知
            if crit_name_vc:
                ch_id = get_level_channel_id(guild_id)
                crit_ch = member.guild.get_channel(ch_id) if ch_id else None
                if crit_ch:
                    try:
                        await crit_ch.send(
                            f"{crit_name_vc} {member.display_name} **+{gain:,}XP**（VC {crit_multi_vc}倍！）"
                        )
                    except discord.DiscordServerError:
                        pass

            data[user_id]["xp"] += gain
            data[user_id]["weekly_xp"] += gain
            data[user_id]["weekly_vc_xp"] = data[user_id].get("weekly_vc_xp", 0) + gain

            await check_level_up(member, data, user_id)
            save_data(guild_id, data)

            boss = load_boss(guild_id)
            if boss.get("active"):
                boss["damage"][user_id] = boss["damage"].get(user_id, 0) + gain
                boss["hp"] = max(0, boss["hp"] - gain)
                if boss["hp"] <= 0:
                    boss["active"] = False
                    boss["cleared"] += 1
                    save_boss(guild_id, boss)
                    await handle_boss_clear(member.guild, boss)
                else:
                    save_boss(guild_id, boss)

            # イベントボスへのダメージ
            event_boss = load_event_boss(guild_id)
            if event_boss.get("active"):
                event_boss["damage"][user_id] = event_boss["damage"].get(user_id, 0) + gain
                event_boss["hp"] = max(0, event_boss["hp"] - gain)
                if event_boss["hp"] <= 0:
                    event_boss["active"] = False
                    save_event_boss(guild_id, event_boss)
                    await handle_event_boss_clear(member.guild, event_boss)
                else:
                    save_event_boss(guild_id, event_boss)

            # サーバーリンクボスへのダメージ
            global_boss = load_global_event_boss()
            if global_boss.get("active"):
                global_boss["damage"][user_id] = global_boss["damage"].get(user_id, 0) + gain
                global_boss["hp"] = max(0, global_boss["hp"] - gain)
                if global_boss["hp"] <= 0:
                    global_boss["active"] = False
                    save_global_event_boss(global_boss)
                    await handle_global_event_boss_clear(global_boss)
                else:
                    save_global_event_boss(global_boss)

    if before.channel and not after.channel:
        vc_users[ck] = False
        vc_afk_flags.pop(ck, None)
        vc_last_xp_time.pop(ck, None)


# =========================
# /coins /buffs /shop /buy
# =========================
@bot.tree.command(name="coins", description="\u6240\u6301\u30b3\u30a4\u30f3\u3092\u78ba\u8a8d\u3057\u307e\u3059")
async def coins(interaction: discord.Interaction):
    data = load_data(interaction.guild.id)
    user_id = str(interaction.user.id)
    info = ensure_user_data(data, user_id)
    save_data(interaction.guild.id, data)

    embed = discord.Embed(title="\U0001f4b0 \u6240\u6301\u30b3\u30a4\u30f3", color=discord.Color.gold())
    embed.add_field(name="\u73fe\u5728\u306e\u6240\u6301\u30b3\u30a4\u30f3", value=f"{info.get('coins', 0):,}\u30b3\u30a4\u30f3", inline=False)
    embed.add_field(name="\u4eca\u65e5\u306e\u7372\u5f97\u91cf", value=f"{info.get('coin_daily_earned', 0):,} / {COIN_DAILY_CAP:,}\u30b3\u30a4\u30f3", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="buffs", description="\u6709\u52b9\u306a\u30a2\u30a4\u30c6\u30e0\u52b9\u679c\u3092\u78ba\u8a8d\u3057\u307e\u3059")
async def buffs(interaction: discord.Interaction):
    data = load_data(interaction.guild.id)
    user_id = str(interaction.user.id)
    info = ensure_user_data(data, user_id)
    cleanup_expired_buffs(info)
    save_data(interaction.guild.id, data)

    if not info.get("buffs"):
        await interaction.response.send_message("\u73fe\u5728\u6709\u52b9\u306a\u30d0\u30d5\u306f\u3042\u308a\u307e\u305b\u3093\u3002", ephemeral=True)
        return

    lines = []
    current = now_ts()
    for buff_type, buff in info["buffs"].items():
        remain = max(0, buff.get("expires_at", 0) - current)
        minutes = math.ceil(remain / 60)
        item = SHOP_ITEMS.get(buff.get("item_id"), {})
        lines.append(f"**{item.get('name', buff_type)}**\uff1a\u6b8b\u308a\u7d04{minutes}\u5206")

    embed = discord.Embed(title="\u2728 \u6709\u52b9\u306a\u30d0\u30d5", description="\n".join(lines), color=discord.Color.blue())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="shop", description="\u30b7\u30e7\u30c3\u30d7\u306e\u5546\u54c1\u4e00\u89a7\u3092\u8868\u793a\u3057\u307e\u3059")
async def shop(interaction: discord.Interaction):
    embed = discord.Embed(
        title="\U0001f6d2 \u30b7\u30e7\u30c3\u30d7",
        description="\u8cfc\u5165\u3059\u308b\u306b\u306f `/buy item_id` \u3092\u4f7f\u3063\u3066\u304f\u3060\u3055\u3044\u3002\u5546\u54c1ID\u306f\u82f1\u5b57\u306e\u307e\u307e\u5165\u529b\u3057\u307e\u3059\u3002",
        color=discord.Color.green()
    )

    for item_id, item in SHOP_ITEMS.items():
        embed.add_field(
            name=f"{item['name']}\uff5c{item['price']:,}\u30b3\u30a4\u30f3",
            value=f"\u5546\u54c1ID: `{item_id}`\n{item['description']}",
            inline=False,
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="buy", description="\u30b7\u30e7\u30c3\u30d7\u306e\u5546\u54c1\u3092\u8cfc\u5165\u3057\u307e\u3059")
async def buy(interaction: discord.Interaction, item_id: str):
    item_id = item_id.lower().strip()

    if item_id not in SHOP_ITEMS:
        await interaction.response.send_message(
            "\u305d\u306e\u5546\u54c1ID\u306f\u5b58\u5728\u3057\u307e\u305b\u3093\u3002`/shop` \u3067\u5546\u54c1\u4e00\u89a7\u3092\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002",
            ephemeral=True
        )
        return

    data = load_data(interaction.guild.id)
    user_id = str(interaction.user.id)
    info = ensure_user_data(data, user_id)
    item = SHOP_ITEMS[item_id]

    # 特殊アイテムは専用コマンドへリダイレクト
    if item_id == "mystery_box":
        await interaction.response.send_message(
            "🎁 ミステリーボックスは `/openbox` コマンドで購入・使用できます！",
            ephemeral=True
        )
        return
    if item_id == "investment_pack":
        await interaction.response.send_message(
            "📈 投資パックは `/invest` コマンドで購入・使用できます！",
            ephemeral=True
        )
        return
    if item_id == "royal_pass":
        await interaction.response.send_message(
            "👑 ROYAL PASSは `/buyroyal` コマンドで購入できます！",
            ephemeral=True
        )
        return

    if not spend_coins(data, user_id, item["price"], f"buy_{item_id}"):
        await interaction.response.send_message(
            f"\u30b3\u30a4\u30f3\u304c\u8db3\u308a\u307e\u305b\u3093\u3002\n"
            f"\u5fc5\u8981: **{item['price']:,}\u30b3\u30a4\u30f3**\n"
            f"\u6240\u6301: **{info.get('coins', 0):,}\u30b3\u30a4\u30f3**",
            ephemeral=True
        )
        return

    # ランクダウン防止シールドは使い切り型（duration不要）
    if item_id == "rankdown_shield":
        info.setdefault("buffs", {})
        info["buffs"]["rankdown_shield"] = {"active": True}
    else:
        add_timed_buff(info, item["buff_type"], item["value"], item["duration"], item_id)

    # 週間コイン消費ランキング用に記録
    info["weekly_coins_spent"] = info.get("weekly_coins_spent", 0) + item["price"]

    # 人気アイテムランキング用に記録（サーバー共通ファイル）
    shop_log = load_shop_log(interaction.guild.id)
    week_key = datetime.now(JST).strftime("%Y-W%W")
    shop_log.setdefault(week_key, {})
    shop_log[week_key][item_id] = shop_log[week_key].get(item_id, 0) + 1
    save_shop_log(interaction.guild.id, shop_log)

    save_data(interaction.guild.id, data)

    duration_min = item["duration"] // 60
    await interaction.response.send_message(
        f"✅ **{item['name']}** を購入しました！\n"
        f"効果: {item['description']}",
        ephemeral=True
    )

    # バフ開始通知
    ch_id = get_level_channel_id(interaction.guild.id)
    notify_ch = interaction.guild.get_channel(ch_id) if ch_id else None
    if notify_ch:
        await notify_ch.send(
            f"✨ **{interaction.user.display_name}** が **{item['name']}** を使用しました！"
            f"（有効時間　{duration_min}分）"
        )

    # バフ終了通知タスク
    asyncio.create_task(notify_buff_end(
        interaction.guild, interaction.user.display_name,
        item["name"], item["duration"]
    ))

# =========================
# バフ終了通知
# =========================
async def notify_buff_end(guild, user_name, item_name, duration_seconds):
    await asyncio.sleep(duration_seconds)
    ch_id = get_level_channel_id(guild.id)
    notify_ch = guild.get_channel(ch_id) if ch_id else None
    if notify_ch:
        await notify_ch.send(f"⏱ **{user_name}** の **{item_name}** の効果が終了しました。")


# =========================
# /return（AFK解除）
# =========================
@bot.tree.command(name="return", description="寝落ちチェックで停止したVC XPを再開します")
async def vc_return(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id = str(interaction.user.id)
    ck = f"{guild_id}:{user_id}"

    if not vc_afk_flags.get(ck):
        await interaction.response.send_message(
            "✅ XPは停止していません！そのままVCをお楽しみください。",
            ephemeral=True
        )
        return

    vc_afk_flags.pop(ck, None)
    await interaction.response.send_message(
        f"🎉 おかえりなさい！ VC XPを再開します！",
        ephemeral=True
    )

    # 通知チャンネルにも表示
    ch_id = get_level_channel_id(guild_id)
    notify_ch = interaction.guild.get_channel(ch_id) if ch_id else None
    if notify_ch:
        await notify_ch.send(
            f"✅ **{interaction.user.display_name}** が復帰しました！VC XPを再開します🎮"
        )

# =========================
# /rank
# =========================
@bot.tree.command(name="rank", description="自分のレベルを確認")
async def rank(interaction: discord.Interaction):
    await interaction.response.defer()
    data = load_data(interaction.guild.id)
    user_id = str(interaction.user.id)
    if user_id not in data:
        await interaction.followup.send("まだデータがありません！")
        return

    xp = data[user_id].get("xp", 0)
    level = data[user_id].get("level", 1)
    required_xp = level * 100
    progress = xp / required_xp
    filled = int(20 * progress)
    bar = "█" * filled + "░" * (20 - filled)
    percent = int(progress * 100)

    embed = discord.Embed(title="📊 あなたのランク情報", color=discord.Color.blue())
    embed.add_field(name="レベル", value=f"Lv {level}", inline=True)
    embed.add_field(name="XPバー", value=f"{bar} {percent}%\n{xp} / {required_xp}", inline=False)
    await interaction.followup.send(embed=embed)

# =========================
# /top
# =========================
@bot.tree.command(name="top", description="XPランキングTOP10")
async def top(interaction: discord.Interaction):
    await interaction.response.defer()
    users = load_data(interaction.guild.id)

    ranking = sorted(
        [(uid, info) for uid, info in users.items() if uid != LAST_DECAY_KEY],
        key=lambda x: (x[1].get("level", 1), x[1].get("xp", 0)),
        reverse=True
    )

    embed = discord.Embed(title="🏆 XPランキング TOP10", color=discord.Color.gold())
    medals = ["🥇", "🥈", "🥉"]
    text = ""

    for i, (user_id, info) in enumerate(ranking[:10], start=1):
        level = info.get("level", 1)
        xp = info.get("xp", 0)
        icon = medals[i-1] if i <= 3 else f"{i}."
        text += f"{icon} <@{user_id}> | Lv{level} | {xp}XP\n"

    embed.description = text
    await interaction.followup.send(embed=embed)

# =========================
# /myxp
# =========================
@bot.tree.command(name="myxp", description="自分のXPやレベルを確認")
async def myxp(interaction: discord.Interaction):
    data = load_data(interaction.guild.id)
    user_id = str(interaction.user.id)
    if user_id not in data:
        await interaction.response.send_message("まだデータがありません！")
        return

    info = data[user_id]
    streak = info.get("login_streak", 0)

    if streak >= 5:
        streak_display = f"🌟 {streak}日（MAXボーナス中！）"
    elif streak >= 2:
        streak_display = f"🔥 {streak}日連続"
    else:
        streak_display = f"{streak}日"

    next_streak = streak + 1
    if next_streak <= 1:
        next_bonus = 100
    elif next_streak == 2:
        next_bonus = 200
    elif next_streak == 3:
        next_bonus = 300
    elif next_streak == 4:
        next_bonus = 500
    else:
        next_bonus = 1000

    embed = discord.Embed(title=f"📊 {interaction.user.name} のデータ", color=discord.Color.green())
    embed.add_field(name="レベル", value=f"Lv {info.get('level', 1)}")
    embed.add_field(name="XP", value=f"{info.get('xp', 0)} XP")
    embed.add_field(name="今週のXP", value=f"{info.get('weekly_xp', 0)} XP")
    embed.add_field(name="連続ログイン", value=streak_display)
    embed.add_field(name="次回デイリーボーナス", value=f"+{next_bonus} XP")
    embed.add_field(name="最終デイリーボーナス", value=info.get("last_daily", "なし"))
    await interaction.response.send_message(embed=embed)

# =========================
# /weeklynote
# =========================
@bot.tree.command(name="weeklynote", description="今週の活動レポートを確認")
async def weeklynote(interaction: discord.Interaction):
    await interaction.response.defer()
    guild_id = interaction.guild.id
    user_id = str(interaction.user.id)
    data = load_data(guild_id)

    if user_id not in data:
        await interaction.followup.send("まだデータがありません！メッセージを送ってからお試しください。")
        return

    info = data[user_id]

    # 今週のXP・順位
    weekly_xp = info.get("weekly_xp", 0)
    sorted_users = sorted(
        [(uid, d) for uid, d in data.items() if uid != LAST_DECAY_KEY],
        key=lambda x: x[1].get("weekly_xp", 0),
        reverse=True
    )
    current_rank = next((i+1 for i, (uid, _) in enumerate(sorted_users) if uid == user_id), 0)
    total_users = len(sorted_users)

    # 前週比
    last_xp = info.get("last_weekly_xp", 0)
    last_rank = info.get("last_weekly_rank", 0)
    xp_diff = weekly_xp - last_xp
    xp_diff_str = f"+{xp_diff}" if xp_diff >= 0 else str(xp_diff)

    if last_rank == 0:
        rank_diff_str = "（前週データなし）"
    else:
        rank_diff = last_rank - current_rank  # 正=上昇
        if rank_diff > 0:
            rank_diff_str = f"↑{rank_diff}"
        elif rank_diff < 0:
            rank_diff_str = f"↓{abs(rank_diff)}"
        else:
            rank_diff_str = "→ 変動なし"

    # アクティブ日数
    active_days = len(info.get("weekly_active_days", []))

    # チャット・VC比率
    chat_xp = info.get("weekly_chat_xp", 0)
    vc_xp = info.get("weekly_vc_xp", 0)
    total_activity_xp = chat_xp + vc_xp
    if total_activity_xp > 0:
        chat_pct = int(chat_xp / total_activity_xp * 100)
        vc_pct = 100 - chat_pct
    else:
        chat_pct = vc_pct = 0

    # ボスダメージ
    boss = load_boss(guild_id)
    boss_dmg = boss.get("damage", {}).get(user_id, 0) if boss.get("active") else 0
    if boss.get("active") and boss_dmg > 0:
        sorted_dmg = sorted(boss["damage"].items(), key=lambda x: x[1], reverse=True)
        boss_rank = next((i+1 for i, (uid, _) in enumerate(sorted_dmg) if uid == user_id), 0)
        boss_str = f"{boss_dmg:,} ({boss_rank}位)"
    elif boss.get("active"):
        boss_str = "未参加"
    else:
        boss_str = "今週ボスなし"

    embed = discord.Embed(
        title="📊 週間レポート",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    embed.add_field(
        name="👤 基本情報",
        value=(
            f"・週間XP: **{weekly_xp:,}**\n"
            f"・順位: **{current_rank}位** / {total_users}人\n"
            f"・アクティブ日数: **{active_days}日**"
        ),
        inline=False
    )
    embed.add_field(
        name="📈 前週比",
        value=(
            f"・XP: **{xp_diff_str}**\n"
            f"・順位: **{rank_diff_str}**"
        ),
        inline=False
    )
    embed.add_field(
        name="🎯 活動分析",
        value=(
            f"・チャット: **{chat_pct}%** ({chat_xp:,} XP)\n"
            f"・VC: **{vc_pct}%** ({vc_xp:,} XP)"
        ),
        inline=False
    )
    embed.add_field(
        name="⚔️ ボス",
        value=f"・ダメージ: **{boss_str}**",
        inline=False
    )
    embed.set_footer(text=f"集計期間: 月曜リセット ／ Lv{info.get('level', 1)}")
    await interaction.followup.send(embed=embed)

# =========================
# /userdata（管理者用）
# =========================
@bot.tree.command(name="userdata", description="ユーザーのデータを確認（管理者用）")
@discord.app_commands.checks.has_permissions(administrator=True)
async def userdata(interaction: discord.Interaction, member: discord.Member):
    data = load_data(interaction.guild.id)
    user_id = str(member.id)

    if user_id not in data:
        await interaction.response.send_message(f"{member.name} のデータはまだありません！", ephemeral=True)
        return

    info = data[user_id]
    level = info.get("level", 1)
    xp = info.get("xp", 0)
    required_xp = level * 100
    progress = xp / required_xp if required_xp > 0 else 0
    filled = int(20 * progress)
    bar = "█" * filled + "░" * (20 - filled)

    current_rank = "なし"
    for min_lv, max_lv, role_name in rank_roles:
        if min_lv <= level <= max_lv:
            current_rank = role_name
            break

    boss = load_boss(interaction.guild.id)
    boss_dmg = boss.get("damage", {}).get(user_id, 0) if boss.get("active") else 0

    streak = info.get("login_streak", 0)
    if streak >= 5:
        streak_display = f"🌟 {streak}日（MAX）"
    elif streak >= 2:
        streak_display = f"🔥 {streak}日連続"
    else:
        streak_display = f"{streak}日"

    embed = discord.Embed(title=f"🔍 {member.name} のデータ", color=discord.Color.orange())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="レベル", value=f"Lv {level}")
    embed.add_field(name="ランク", value=current_rank)
    embed.add_field(name="XP", value=f"{xp} / {required_xp}\n{bar} {int(progress*100)}%", inline=False)
    embed.add_field(name="今週のXP", value=f"{info.get('weekly_xp', 0)} XP")
    embed.add_field(name="連続ログイン", value=streak_display)
    embed.add_field(name="最終デイリー", value=info.get("last_daily", "なし"))
    embed.add_field(name="今週のボスダメージ", value=f"{boss_dmg} ダメージ")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@userdata.error
async def userdata_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("このコマンドは管理者のみ使用できます！", ephemeral=True)

# =========================
# /alldata（管理者用）
# =========================
@bot.tree.command(name="alldata", description="全ユーザーデータをCSVで出力（管理者用）")
@discord.app_commands.checks.has_permissions(administrator=True)
async def alldata(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    data = load_data(interaction.guild.id)
    guild = interaction.guild

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["UserID", "Username", "Level", "XP", "WeeklyXP", "LastDaily", "LoginStreak"])

    for uid, info in data.items():
        if uid == LAST_DECAY_KEY:
            continue
        member = guild.get_member(int(uid))
        username = member.name if member else f"Unknown({uid})"
        writer.writerow([
            uid,
            username,
            info.get("level", 1),
            info.get("xp", 0),
            info.get("weekly_xp", 0),
            info.get("last_daily", ""),
            info.get("login_streak", 0)
        ])

    output.seek(0)
    now_str = datetime.now(JST).strftime("%Y%m%d_%H%M")
    filename = f"userdata_{guild.id}_{now_str}.csv"
    file = discord.File(
        fp=io.BytesIO(output.getvalue().encode("utf-8-sig")),
        filename=filename
    )
    user_count = sum(1 for k in data if k != LAST_DECAY_KEY)
    await interaction.followup.send(
        f"📊 全ユーザーデータです！（{user_count}人分）",
        file=file,
        ephemeral=True
    )

@alldata.error
async def alldata_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("このコマンドは管理者のみ使用できます！", ephemeral=True)

# =========================
# 週間ランキング（全サーバー）
# =========================
@tasks.loop(minutes=1)
async def weekly_ranking_task():
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")

    if not (now.weekday() == 0 and now.hour == 18 and now.minute == 0):
        return

    for guild in bot.guilds:
        gid = guild.id
        if _weekly_announced.get(gid) == today:
            continue
        _weekly_announced[gid] = today

        data = load_data(gid)
        if not data:
            continue

        ch_id = get_level_channel_id(gid)
        notify_channel = guild.get_channel(ch_id) if ch_id else None

        sorted_users = sorted(
            [(uid, info) for uid, info in data.items() if uid != LAST_DECAY_KEY],
            key=lambda x: x[1].get("weekly_xp", 0),
            reverse=True
        )
        top3 = sorted_users[:3]

        for role_name in weekly_roles.values():
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                for member in role.members:
                    try:
                        await member.remove_roles(role)
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        weekly_coin_rewards = {1: 3000, 2: 2000, 3: 1000}
        text = ""
        for i, (user_id, info) in enumerate(top3, start=1):
            role = discord.utils.get(guild.roles, name=weekly_roles[i])
            member = guild.get_member(int(user_id))
            if role and member:
                try:
                    await member.add_roles(role)
                except (discord.Forbidden, discord.HTTPException):
                    pass
            coin_r = weekly_coin_rewards.get(i, 0)
            info["coins"] = info.get("coins", 0) + coin_r
            info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + coin_r
            text += f"{['🥇','🥈','🥉'][i-1]} <@{user_id}> - {info.get('weekly_xp', 0)} XP 💰 +{coin_r:,}コイン\n"

        if notify_channel:
            embed = discord.Embed(
                title="🏆 週間ランキング結果発表！",
                description=text,
                color=discord.Color.gold()
            )
            await notify_channel.send(embed=embed)

        # 前週データを保存してリセット
        for i, (uid, info) in enumerate(sorted_users, start=1):
            info["last_weekly_xp"] = info.get("weekly_xp", 0)
            info["last_weekly_rank"] = i

        # 活動量ボーナス（週1000XP以上 → 500コイン）
        activity_bonus_users = ""
        for uid, info in data.items():
            if uid == LAST_DECAY_KEY:
                continue
            if info.get("weekly_xp", 0) >= 1000:
                info["coins"] = info.get("coins", 0) + 500
                info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + 500
                activity_bonus_users += f"<@{uid}> +500コイン\n"

        if notify_channel and activity_bonus_users:
            embed_act = discord.Embed(
                title="🎯 週間活動ボーナス！",
                description=f"今週1000XP以上獲得したメンバーへ💰\n\n{activity_bonus_users}",
                color=discord.Color.green()
            )
            await notify_channel.send(embed=embed_act)

        for uid in data:
            if uid != LAST_DECAY_KEY:
                data[uid]["weekly_xp"] = 0
                data[uid]["weekly_chat_xp"] = 0
                data[uid]["weekly_vc_xp"] = 0
                data[uid]["weekly_active_days"] = []
                data[uid]["weekly_coins_spent"] = 0
                data[uid]["weekly_coins_earned"] = 0
                data[uid]["coin_daily_earned"] = 0
        save_data(gid, data)

# =========================
# XP Decay Task
# =========================
_decay_fired = {}  # { "YYYY-MM-DD": True }

@tasks.loop(minutes=1)
async def decay_task():
    await bot.wait_until_ready()
    now = datetime.now(JST)
    if not (now.hour == 9 and now.minute == 0):
        return
    today = now.strftime("%Y-%m-%d")
    if _decay_fired.get(today):
        return
    _decay_fired[today] = True

    for guild in bot.guilds:
        gid = guild.id
        data = load_data(gid)
        if not data:
            continue

        for uid, info in data.items():
            if uid == LAST_DECAY_KEY or not isinstance(info, dict):
                continue

            # 毎日9時にコイン日次獲得上限をリセット
            info["coin_daily_earned"] = 0

            # アクティブ判定（今日メッセージを送ったか）
            last_active = info.get("last_active_date", "")
            is_active_today = (last_active == today)

            if is_active_today:
                # アクティブ日は減衰なし・streak リセット
                info["level_down_streak"] = 0
                continue

            # XP減衰ガードが有効なら減衰スキップ
            cleanup_expired_buffs(info)
            if info.get("buffs", {}).get("decay_guard"):
                info["level_down_streak"] = 0
                continue

            # 非アクティブ日 → B+C方式で減衰
            current_xp = info.get("xp", 0)
            current_level = info.get("level", 1)

            if current_xp > 0:
                # レベル帯別の減衰率（次レベル必要XPの○%）
                if current_level < 30:
                    rate = 0.01
                elif current_level < 100:
                    rate = 0.02
                elif current_level < 200:
                    rate = 0.03
                else:
                    rate = 0.04

                next_level_xp = current_level * 100  # 次レベルに必要なXP
                decay_amount = max(1, int(next_level_xp * rate))
                info["xp"] = max(0, current_xp - decay_amount)

        save_data(gid, data)

        # ROYAL PASSの期限切れロールを削除
        for uid, info in data.items():
            if uid == LAST_DECAY_KEY or not isinstance(info, dict):
                continue
            if not info.get("buffs", {}).get("royal_pass"):
                member = guild.get_member(int(uid))
                if member:
                    role = discord.utils.get(guild.roles, name=ROYAL_PASS_ROLE)
                    if role and role in member.roles:
                        try:
                            await member.remove_roles(role)
                        except (discord.Forbidden, discord.HTTPException):
                            pass

        save_data(gid, data)

# =========================
# サーバーリンクボス 期間終了チェックタスク
# =========================
@tasks.loop(minutes=1)
async def server_link_boss_expiry_task():
    await bot.wait_until_ready()
    global_boss = load_global_event_boss()
    if not global_boss.get("active"):
        return

    end_at = global_boss.get("end_at", 0)
    if end_at and time.time() < end_at:
        return

    # 期間終了 → 未討伐のまま終了
    global_boss["active"] = False
    save_global_event_boss(global_boss)

    boss_name = global_boss.get("name", "大魔王")
    max_hp    = global_boss.get("max_hp", 0)
    hp        = global_boss.get("hp", 0)
    pct       = int((max_hp - hp) / max_hp * 100) if max_hp > 0 else 0

    medals   = ["🥇", "🥈", "🥉"]
    top3     = sorted(global_boss.get("damage", {}).items(), key=lambda x: x[1], reverse=True)[:3]
    top_text = "\n".join(
        f"{medals[i]} {get_member_name(bot, uid)} … {dmg:,}ダメージ"
        for i, (uid, dmg) in enumerate(top3)
    ) or "なし"

    embed = discord.Embed(
        title=f"⏰ サーバーリンクボス【{boss_name}】開催終了",
        description=(
            f"開催期間が終了しました。討伐ならず…\n\n"
            f"残りHP：**{hp:,} / {max_hp:,}**（**{pct}%** 削った！）\n\n"
            f"**最終貢献者TOP3**\n{top_text}"
        ),
        color=discord.Color.greyple()
    )

    for guild in bot.guilds:
        ch_id = get_level_channel_id(guild.id)
        notify_channel = guild.get_channel(ch_id) if ch_id else None
        if notify_channel:
            try:
                await notify_channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

# =========================
# デイリーミッション朝7時通知タスク
# =========================
_daily_mission_announced = {}  # { "YYYY-MM-DD": True }

@tasks.loop(minutes=1)
async def daily_mission_announce_task():
    await bot.wait_until_ready()
    now = datetime.now(JST)
    if not (now.hour == 7 and now.minute == 0):
        return
    today = now.strftime("%Y-%m-%d")
    if _daily_mission_announced.get(today):
        return
    _daily_mission_announced[today] = True

    mission  = get_today_mission()
    weekdays = ["月", "火", "水", "木", "金", "土", "日"]
    weekday  = weekdays[now.weekday()]

    embed = discord.Embed(
        title=f"🌅 今日のデイリーミッション（{weekday}曜日）",
        description=(
            f"**{mission['label']}**\n\n"
            f"💰 達成報酬：**{mission['reward']:,}コイン**\n\n"
            f"`/dailymission` で達成確認・受け取りができます！"
        ),
        color=discord.Color.orange()
    )
    embed.set_footer(text="毎日達成してコインを貯めよう！")

    for guild in bot.guilds:
        ch_id = get_level_channel_id(guild.id)
        notify_channel = guild.get_channel(ch_id) if ch_id else None
        if notify_channel:
            try:
                await notify_channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass
# =========================
_rankdown_check_fired = {}  # { "YYYY-MM-DD": True }

@tasks.loop(minutes=1)
async def rankdown_check_task():
    await bot.wait_until_ready()
    now = datetime.now(JST)
    if not (now.hour == 18 and now.minute == 0):
        return
    today = now.strftime("%Y-%m-%d")
    if _rankdown_check_fired.get(today):
        return
    _rankdown_check_fired[today] = True

    for guild in bot.guilds:
        gid = guild.id
        data = load_data(gid)
        if not data:
            continue
        for uid in list(data.keys()):
            if uid == LAST_DECAY_KEY:
                continue
            await check_level_down(guild, data, uid)
        save_data(gid, data)
# =========================
# XP BOOST TASK（全サーバー）
# 毎日ランダムな時間帯に2回発動（朝8-11時・夜18-22時）
# =========================

# 当日のブースト予定時刻を保持 { "YYYY-MM-DD": [hour1, hour2] }
_boost_schedule = {}
# 発動済みフラグ { "YYYY-MM-DD_hour": True }
_boost_fired = {}

@tasks.loop(minutes=1)
async def xp_boost_scheduler():
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")

    # 当日のスケジュールをまだ決めていなければ決める
    if today not in _boost_schedule:
        morning_hour = random.randint(8, 11)
        night_hour = random.randint(18, 22)
        _boost_schedule[today] = [morning_hour, night_hour]
        # 前日以前のスケジュールを削除
        for key in list(_boost_schedule.keys()):
            if key < today:
                del _boost_schedule[key]
        for key in list(_boost_fired.keys()):
            if key.split("_")[0] < today:
                del _boost_fired[key]

    for hour in _boost_schedule[today]:
        fire_key = f"{today}_{hour}"
        if _boost_fired.get(fire_key):
            continue
        if now.hour == hour and now.minute == 0:
            _boost_fired[fire_key] = True
            multiplier = 3 if random.random() < 0.05 else 2

            for guild in bot.guilds:
                set_time_boost(guild.id, multiplier)
                ch_id = get_level_channel_id(guild.id)
                channel = guild.get_channel(ch_id) if ch_id else None
                if channel:
                    await channel.send(
                        f"🔥 **XP BOOST START!**\n"
                        f"XPが **{multiplier}倍** になりました！\n"
                        f"1時間限定！"
                    )

            # 1時間後に終了
            await asyncio.sleep(3600)

            for guild in bot.guilds:
                set_time_boost(guild.id, 1)
                ch_id = get_level_channel_id(guild.id)
                channel = guild.get_channel(ch_id) if ch_id else None
                if channel:
                    await channel.send("⏱ **XP BOOST 終了！**")

# =========================
# 週間ランキング中間発表（全サーバー・毎日21時）
# =========================
@tasks.loop(minutes=1)
async def weekly_mid_announcement():
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")

    if not (now.hour == 21 and now.minute == 0):
        return

    for guild in bot.guilds:
        gid = guild.id
        if _mid_announced_today.get(gid) == today:
            continue
        _mid_announced_today[gid] = today

        data = load_data(gid)
        if not data:
            continue

        ch_id = get_level_channel_id(gid)
        notify_channel = guild.get_channel(ch_id) if ch_id else None

        sorted_users = sorted(
            [(uid, info) for uid, info in data.items() if uid != LAST_DECAY_KEY],
            key=lambda x: x[1].get("weekly_xp", 0),
            reverse=True
        )

        desc = ""
        medals = ["🥇", "🥈", "🥉", "④", "⑤"]
        for i, (uid, info) in enumerate(sorted_users[:5]):
            desc += f"{medals[i]} <@{uid}> - {info.get('weekly_xp', 0)} XP\n"

        if notify_channel:
            embed = discord.Embed(
                title="📊 週間ランキング中間発表",
                description=desc,
                color=discord.Color.blue()
            )
            embed.set_footer(text="最終結果は月曜18:00に発表！")
            await notify_channel.send(embed=embed)

# =========================
# イベントボス read/write
# =========================
GLOBAL_EVENT_BOSS_FILE = os.path.join(DATA_DIR, "global_event_boss.json")

def load_global_event_boss():
    if os.path.exists(GLOBAL_EVENT_BOSS_FILE):
        with open(GLOBAL_EVENT_BOSS_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                pass
    return {"active": False, "hp": 0, "max_hp": 0, "name": "", "damage": {}, "boost_days": 7, "boost_multiplier": 3}

def save_global_event_boss(boss):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(GLOBAL_EVENT_BOSS_FILE, "w", encoding="utf-8") as f:
        json.dump(boss, f, ensure_ascii=False, indent=2)

def load_event_boss(guild_id):
    path = event_boss_file(guild_id)
    if not os.path.exists(path):
        return {"active": False, "hp": 0, "max_hp": 0, "damage": {}, "name": "大魔王", "consecutive_clears": 0}
    with open(path, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"active": False, "hp": 0, "max_hp": 0, "damage": {}, "name": "大魔王", "consecutive_clears": 0}

def save_event_boss(guild_id, boss):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(event_boss_file(guild_id), "w") as f:
        json.dump(boss, f, indent=4)

# =========================
# イベントボス：自動発動チェック（通常ボスクリア時に呼ぶ）
# =========================
async def check_event_boss_trigger(guild, consecutive_clears):
    gid = guild.id
    event_boss = load_event_boss(gid)

    # すでにイベントボス発動中なら無視
    if event_boss.get("active"):
        return

    # 連続クリア数を更新
    event_boss["consecutive_clears"] = consecutive_clears
    save_event_boss(gid, event_boss)

    # トリガー条件チェック（累計クリア数が5の倍数に達したら発動）
    if consecutive_clears > 0 and consecutive_clears % EVENT_BOSS_CONSECUTIVE_CLEARS == 0:
        await spawn_event_boss(guild, event_boss.get("name", "大魔王"))

async def spawn_event_boss(guild, boss_name, hp=None, days=None, boost_multiplier=None):
    gid = guild.id
    ch_id = get_level_channel_id(gid)
    notify_channel = guild.get_channel(ch_id) if ch_id else None

    event_hp = hp if hp else EVENT_BOSS_DEFAULT_HP
    boost_days = days if days else EVENT_BOSS_BOOST_DAYS
    boost_multi = boost_multiplier if boost_multiplier else EVENT_BOSS_BOOST_MULTIPLIER

    event_boss = {
        "active": True,
        "hp": event_hp,
        "max_hp": event_hp,
        "damage": {},
        "name": boss_name,
        "boost_days": boost_days,
        "boost_multiplier": boost_multi,
        "consecutive_clears": EVENT_BOSS_CONSECUTIVE_CLEARS
    }
    save_event_boss(gid, event_boss)
    event_boss_active[gid] = True

    # 限定ロールを作成（なければ）
    role = discord.utils.get(guild.roles, name=EVENT_BOSS_CLEAR_ROLE)
    if not role:
        try:
            role = await guild.create_role(
                name=EVENT_BOSS_CLEAR_ROLE,
                color=discord.Color.from_rgb(255, 215, 0),
                reason="イベントボス自動生成"
            )
        except discord.Forbidden:
            pass

    if notify_channel:
        embed = discord.Embed(
            title=f"🚨 イベントボス出現！【{boss_name}】",
            description=(
                f"通常ボスを**{EVENT_BOSS_CONSECUTIVE_CLEARS}回クリア**したことで\n"
                f"伝説の強敵が目覚めた！\n\n"
                f"メッセージを送るだけで自動攻撃！\n"
                f"討伐成功で限定ロールとXP{boost_multi}倍ブーストをGET！"
            ),
            color=discord.Color.from_rgb(255, 100, 0)
        )
        embed.add_field(name="❤️ HP", value=f"{event_hp:,} / {event_hp:,}")
        embed.add_field(name="📅 開催期間", value=f"{boost_days}日間")
        embed.add_field(name="🎁 討伐報酬", value=f"`{EVENT_BOSS_CLEAR_ROLE}` ロール\nXP **{boost_multi}倍**（{boost_days}日間）", inline=False)
        embed.set_footer(text="全員で力を合わせて倒せ！")
        await notify_channel.send("@everyone", embed=embed)

async def handle_global_event_boss_clear(boss):
    """サーバーリンクボス討伐処理"""
    boss_name   = boss.get("name", "大魔王")
    boost_multi = boss.get("boost_multiplier", 3)
    boost_days  = boss.get("boost_days", 7)

    # 全サーバーに討伐通知・ブースト付与
    for guild in bot.guilds:
        ch_id = get_level_channel_id(guild.id)
        notify_channel = guild.get_channel(ch_id) if ch_id else None

        # 討伐者ロール付与（条件チェックあり）
        role = discord.utils.get(guild.roles, name=EVENT_BOSS_CLEAR_ROLE)
        if not role:
            try:
                role = await guild.create_role(
                    name=EVENT_BOSS_CLEAR_ROLE,
                    color=discord.Color.from_rgb(255, 215, 0),
                    reason="サーバーリンクボス討伐"
                )
            except discord.Forbidden:
                role = None

        data = load_data(guild.id)
        reward_xp       = boss.get("reward_xp", 0)
        boost_duration  = boost_days * 24 * 60 * 60

        for uid, dmg in boss["damage"].items():
            if dmg <= 0:
                continue
            # 報酬条件チェック
            if reward_xp > 0 and dmg < reward_xp:
                continue
            member = guild.get_member(int(uid))

            # ロール付与
            if member and role:
                try:
                    await member.add_roles(role)
                except discord.Forbidden:
                    pass

            # 個人XPブースト付与
            info = ensure_user_data(data, uid)
            add_timed_buff(info, "xp_multiplier", float(boost_multi), boost_duration, "server_link_boss")

        save_data(guild.id, data)

        if notify_channel:
            reward_xp   = boss.get("reward_xp", 0)
            reward_cond = f"\n⚠️ 受取条件：**{reward_xp:,}ダメージ以上** 与えたメンバーのみ" if reward_xp > 0 else ""
            embed = discord.Embed(
                title=f"🎉 サーバーリンクボス 討伐成功！【{boss_name}】",
                description=(
                    f"サーバーリンクボスに参加したメンバーが力を合わせて **{boss_name}** を倒した！\n\n"
                    f"🎁 全サーバーのXPが **{boost_multi}倍**（{boost_days}日間）になります！\n"
                    f"`{EVENT_BOSS_CLEAR_ROLE}` ロールを獲得！{reward_cond}"
                ),
                color=discord.Color.gold()
            )
            try:
                await notify_channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass


# =========================
# =========================
async def handle_event_boss_clear(guild, event_boss):
    gid = guild.id
    ch_id = get_level_channel_id(gid)
    notify_channel = guild.get_channel(ch_id) if ch_id else None

    # 討伐者全員に限定ロール付与
    role = discord.utils.get(guild.roles, name=EVENT_BOSS_CLEAR_ROLE)
    for uid, dmg in event_boss["damage"].items():
        if dmg <= 0:
            continue
        member = guild.get_member(int(uid))
        if member and role:
            try:
                await member.add_roles(role)
            except (discord.Forbidden, discord.HTTPException):
                pass

    # MVPランキング
    sorted_dmg = sorted(event_boss["damage"].items(), key=lambda x: x[1], reverse=True)
    mvp_text = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, dmg) in enumerate(sorted_dmg[:3]):
        mvp_text += f"{medals[i]} <@{uid}> - {dmg:,}ダメージ\n"

    boss_name = event_boss.get("name", "大魔王")

    boost_days = event_boss.get("boost_days", EVENT_BOSS_BOOST_DAYS)
    boost_multi = event_boss.get("boost_multiplier", EVENT_BOSS_BOOST_MULTIPLIER)

    # MVP（1位）への特別称号メッセージ
    mvp_uid = sorted_dmg[0][0] if sorted_dmg else None

    if notify_channel:
        embed = discord.Embed(
            title=f"🏆 イベントボス【{boss_name}】討伐成功！！",
            description=(
                f"伝説の強敵を全員で打ち倒した！\n\n"
                f"**MVPランキング**\n{mvp_text}"
            ),
            color=discord.Color.gold()
        )
        embed.add_field(
            name="🎁 報酬",
            value=(
                f"`{EVENT_BOSS_CLEAR_ROLE}` ロール付与！\n"
                f"🔥 XP **{boost_multi}倍ブースト** {boost_days}日間！"
            )
        )
        await notify_channel.send(embed=embed)

        # MVPへの特別称号メッセージ
        if mvp_uid:
            mvp_dmg = sorted_dmg[0][1]
            await notify_channel.send(
                f"👑 **【伝説の討伐者】**\n"
                f"<@{mvp_uid}> は今回のイベントボス討伐において **{mvp_dmg:,}ダメージ** を叩き出し、\n"
                f"サーバー最強の討伐者として歴史に名を刻んだ！"
            )

    # XPブーストを設定
    asyncio.create_task(event_boss_boost(guild, notify_channel, boost_multi, boost_days))

    # イベントボスをリセット・連続クリア数もリセット
    reset_event = load_event_boss(gid)
    reset_event["active"] = False
    reset_event["consecutive_clears"] = 0
    save_event_boss(gid, reset_event)
    event_boss_active[gid] = False

async def event_boss_boost(guild, notify_channel, multiplier=None, days=None):
    gid = guild.id
    m = multiplier if multiplier else EVENT_BOSS_BOOST_MULTIPLIER
    d = days if days else EVENT_BOSS_BOOST_DAYS
    set_boss_boost(gid, m)
    await asyncio.sleep(d * 24 * 3600)
    set_boss_boost(gid, 1)
    if notify_channel:
        await notify_channel.send(f"⏱ **イベント討伐ブースト終了！** XPが通常に戻りました。")

# =========================
# 週ボス：討伐成功処理
# =========================
async def handle_boss_clear(guild, boss):
    gid = guild.id
    ch_id = get_level_channel_id(gid)
    notify_channel = guild.get_channel(ch_id) if ch_id else None

    # 討伐回数（cleared はまだインクリメント前）
    cleared_count = boss.get("cleared", 0)
    role_name = get_boss_clear_role_name(cleared_count)

    # ロールが既に存在していれば取得、なければ作成
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        try:
            role = await guild.create_role(
                name=role_name,
                color=discord.Color.from_rgb(220, 50, 50),
                reason=f"LV{cleared_count}ボス討伐者ロール自動作成"
            )
        except discord.Forbidden:
            role = None

    # 討伐者全員にロール付与
    for uid, dmg in boss["damage"].items():
        if dmg <= 0:
            continue
        member = guild.get_member(int(uid))
        if member and role:
            try:
                await member.add_roles(role)
            except discord.Forbidden:
                pass

    # ボスデータにロール名を記録（月曜削除用）
    boss["last_clear_role"] = role_name
    save_boss(gid, boss)

    sorted_dmg = sorted(boss["damage"].items(), key=lambda x: x[1], reverse=True)
    mvp_text = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, dmg) in enumerate(sorted_dmg[:3]):
        mvp_text += f"{medals[i]} <@{uid}> - {dmg}ダメージ\n"

    next_hp = int(BOSS_BASE_HP * (BOSS_HP_SCALE ** boss["cleared"]))

    if notify_channel:
        embed = discord.Embed(
            title="🎉 週ボス討伐成功！！",
            description=f"全員で見事ボスを倒しました！\n\n**MVPランキング**\n{mvp_text}",
            color=discord.Color.green()
        )
        embed.add_field(name="報酬", value=f"🔥 **次のボス出現まで XP 2倍ブースト** 発動！\n`{role_name}` ロール付与！")
        embed.set_footer(text=f"次のボスHP: {next_hp:,}")
        await notify_channel.send(embed=embed)

    asyncio.create_task(boss_clear_boost(guild, notify_channel))

    # ボス討伐コイン付与（damage × 0.1）
    data = load_data(gid)
    coin_text = ""
    for uid, dmg in boss["damage"].items():
        if dmg <= 0:
            continue
        coin_reward = int(dmg * 0.1)
        if coin_reward <= 0:
            continue
        info = ensure_user_data(data, uid)
        info["coins"] = info.get("coins", 0) + coin_reward
        info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + coin_reward
        coin_text += f"<@{uid}> +{coin_reward:,}コイン\n"
    save_data(gid, data)

    if notify_channel and coin_text:
        embed_coin = discord.Embed(
            title="💰 ボス討伐コイン報酬",
            description=coin_text,
            color=discord.Color.yellow()
        )
        await notify_channel.send(embed=embed_coin)

    # イベントボストリガーチェック
    await check_event_boss_trigger(guild, boss.get("cleared", 0))

# =========================
# 週ボス：討伐ブースト
# =========================
async def boss_clear_boost(guild, notify_channel):
    gid = guild.id
    set_boss_boost(gid, 2)
    if notify_channel:
        await notify_channel.send("🔥 **討伐記念 XP 2倍ブースト開始！** 次のボス出現まで継続！")

    now = datetime.now(JST)
    days_until_monday = (7 - now.weekday()) % 7 or 7
    next_monday = (now + timedelta(days=days_until_monday)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    wait_seconds = (next_monday - now).total_seconds()
    await asyncio.sleep(wait_seconds)

    set_boss_boost(gid, 1)
    if notify_channel:
        await notify_channel.send("⏱ **討伐ブースト終了！** 新しいボスが出現しました！")

# =========================
# 週ボス：出現タスク（全サーバー・月曜6時）
# =========================
@tasks.loop(minutes=1)
async def boss_spawn_task():
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")

    if not (now.weekday() == 0 and now.hour == 6 and now.minute <= 2):
        return

    for guild in bot.guilds:
        gid = guild.id
        if _boss_spawn_announced.get(gid) == today:
            continue
        _boss_spawn_announced[gid] = today

        ch_id = get_level_channel_id(gid)
        notify_channel = guild.get_channel(ch_id) if ch_id else None

        boss = load_boss(gid)
        boss_was_alive = boss.get("active", False)

        set_boss_boost(gid, 1)
        set_time_boost(gid, 1)

        # 前週の討伐者ロールを剥がして削除
        last_role_name = boss.get("last_clear_role")
        if last_role_name:
            old_role = discord.utils.get(guild.roles, name=last_role_name)
            if old_role:
                for member in guild.members:
                    if old_role in member.roles:
                        try:
                            await member.remove_roles(old_role, reason="週次ボス討伐者ロールリセット")
                        except discord.Forbidden:
                            pass
                try:
                    await old_role.delete(reason="週次ボス討伐者ロール削除")
                except discord.Forbidden:
                    pass
            boss["last_clear_role"] = None
            save_boss(gid, boss)

        cleared = boss.get("cleared", 0)

        if boss_was_alive:
            # 討伐失敗：残りHP + 最大HPの20%回復（最大HPを上限とする）
            old_max_hp = boss.get("max_hp", int(BOSS_BASE_HP * (BOSS_HP_SCALE ** cleared)))
            remaining_hp = boss.get("hp", old_max_hp)
            recover = int(old_max_hp * 0.2)
            new_hp = min(remaining_hp + recover, old_max_hp)
            new_max_hp = old_max_hp  # スケールアップなし

            if notify_channel:
                await notify_channel.send(
                    f"💀 **ボスは討伐されませんでした...**\n"
                    f"ボスが回復して再出現！ HP +{recover:,} 回復！\n"
                    f"今週こそリベンジだ！"
                )
        else:
            # 討伐成功 or 初回：通常スケール
            new_max_hp = int(BOSS_BASE_HP * (BOSS_HP_SCALE ** cleared))
            new_hp = new_max_hp

        new_boss = {
            "active": True,
            "hp": new_hp,
            "max_hp": new_max_hp,
            "damage": {},
            "week": boss.get("week", 0) + 1,
            "cleared": cleared
        }
        save_boss(gid, new_boss)

        if notify_channel:
            if boss_was_alive:
                embed = discord.Embed(
                    title=f"👹 ボスが復活！ Week {new_boss['week']}",
                    description=(
                        "先週倒せなかったボスが回復して戻ってきた！\n\n"
                        "メッセージを送るだけで自動攻撃！\n"
                        "今週こそ討伐せよ！"
                    ),
                    color=discord.Color.dark_red()
                )
                embed.add_field(name="❤️ HP", value=f"{new_hp:,} / {new_max_hp:,}")
                embed.add_field(name="💉 先週の残りHP", value=f"{remaining_hp:,}")
                embed.add_field(name="✨ 回復量", value=f"+{recover:,}")
            else:
                embed = discord.Embed(
                    title=f"👹 週ボス出現！ Week {new_boss['week']}",
                    description=(
                        "ボスが現れた！今週中に倒せ！\n\n"
                        "メッセージを送るだけで自動攻撃！\n"
                        "討伐成功で特別ロールをGET！"
                    ),
                    color=discord.Color.red()
                )
                embed.add_field(name="❤️ HP", value=f"{new_hp:,} / {new_max_hp:,}")

            embed.add_field(name="⚔️ 攻撃方法", value="メッセージ送信 or VC参加で自動攻撃！")
            embed.add_field(name="🎁 討伐報酬", value="次のボス出現まで XP 2倍ブースト ＋ 特別ロール")
            embed.set_footer(text="6時間ごとにダメージ報告あり")
            await notify_channel.send(embed=embed)

# =========================
# 週ボス：ダメージ報告（8時・16時・24時）
# =========================
_boss_report_fired = {}  # { "YYYY-MM-DD_HH": True }

@tasks.loop(minutes=1)
async def boss_damage_report():
    await bot.wait_until_ready()

    now = datetime.now(JST)
    if now.hour not in [8, 16, 0] or now.minute != 0:
        return

    fire_key = now.strftime("%Y-%m-%d_%H")
    if _boss_report_fired.get(fire_key):
        return
    _boss_report_fired[fire_key] = True

    # 古いフラグを削除
    today = now.strftime("%Y-%m-%d")
    for key in list(_boss_report_fired.keys()):
        if key.split("_")[0] < today:
            del _boss_report_fired[key]

    medals = ["🥇", "🥈", "🥉"]

    for guild in bot.guilds:
        gid = guild.id
        ch_id = get_level_channel_id(gid)
        notify_channel = guild.get_channel(ch_id) if ch_id else None
        if not notify_channel:
            continue

        embeds = []

        # ① 通常ボス
        boss = load_boss(gid)
        if boss.get("active"):
            max_hp     = boss.get("max_hp", 1)
            current_hp = boss.get("hp", 0)
            progress   = (max_hp - current_hp) / max_hp
            bar        = "█" * int(20 * progress) + "░" * (20 - int(20 * progress))
            percent    = int(progress * 100)

            sorted_dmg = sorted(boss["damage"].items(), key=lambda x: x[1], reverse=True)
            top_text   = "\n".join(
                f"{medals[i]} <@{uid}> - {dmg:,}ダメージ"
                for i, (uid, dmg) in enumerate(sorted_dmg[:3])
            ) or "まだ誰も攻撃していません！"

            embed = discord.Embed(title=f"⚔️ 週ボス ダメージレポート（Week {boss.get('week', 1)}）", color=discord.Color.orange())
            embed.add_field(name="❤️ ボスHP", value=f"`{bar}` {percent}%\n{current_hp:,} / {max_hp:,}", inline=False)
            embed.add_field(name="🏆 ダメージTOP3", value=top_text, inline=False)
            embed.set_footer(text="メッセージを送って攻撃しよう！")
            embeds.append(embed)

        # ② サーバー個別イベントボス
        event_boss = load_event_boss(gid)
        if event_boss.get("active"):
            eb_max  = event_boss.get("max_hp", 1)
            eb_hp   = event_boss.get("hp", 0)
            eb_pct  = int((eb_max - eb_hp) / eb_max * 100)
            eb_bar  = "█" * int((eb_max - eb_hp) / eb_max * 20) + "░" * int(eb_hp / eb_max * 20)
            eb_name = event_boss.get("name", "大魔王")

            eb_sorted = sorted(event_boss["damage"].items(), key=lambda x: x[1], reverse=True)
            eb_top    = "\n".join(
                f"{medals[i]} <@{uid}> - {dmg:,}ダメージ"
                for i, (uid, dmg) in enumerate(eb_sorted[:3])
            ) or "まだ誰も攻撃していません！"

            embed = discord.Embed(title=f"🚨 イベントボス【{eb_name}】ダメージレポート", color=discord.Color.from_rgb(255, 100, 0))
            embed.add_field(name="❤️ HP", value=f"`{eb_bar}` {eb_pct}%\n{eb_hp:,} / {eb_max:,}", inline=False)
            embed.add_field(name="🏆 ダメージTOP3", value=eb_top, inline=False)
            embed.set_footer(text="全員で力を合わせて倒せ！")
            embeds.append(embed)

        # ③ サーバーリンクボス
        global_boss = load_global_event_boss()
        if global_boss.get("active"):
            gb_max  = global_boss.get("max_hp", 1)
            gb_hp   = global_boss.get("hp", 0)
            gb_pct  = int((gb_max - gb_hp) / gb_max * 100)
            gb_bar  = "█" * int((gb_max - gb_hp) / gb_max * 20) + "░" * int(gb_hp / gb_max * 20)
            gb_name = global_boss.get("name", "大魔王")

            gb_sorted = sorted(global_boss["damage"].items(), key=lambda x: x[1], reverse=True)
            gb_top    = "\n".join(
                f"{medals[i]} {get_member_name(bot, uid)} - {dmg:,}ダメージ"
                for i, (uid, dmg) in enumerate(gb_sorted[:3])
            ) or "まだ誰も攻撃していません！"

            embed = discord.Embed(title=f"🌐 サーバーリンクボス【{gb_name}】ダメージレポート", color=discord.Color.purple())
            embed.add_field(name="❤️ HP", value=f"`{gb_bar}` {gb_pct}%\n{gb_hp:,} / {gb_max:,}", inline=False)
            embed.add_field(name="🏆 ダメージTOP3", value=gb_top, inline=False)
            embed.set_footer(text="全サーバーで力を合わせて倒せ！")
            embeds.append(embed)

        # まとめて送信
        for embed in embeds:
            try:
                await notify_channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass


# =========================
# /chest（ランダム宝箱）
# =========================
chest_cooldowns = {}  # { "guild_id:user_id": timestamp }

@bot.tree.command(name="chest", description="ランダム宝箱を開ける（1時間に1回）")
async def chest(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id = str(interaction.user.id)
    ck = f"{guild_id}:{user_id}"
    now = time.time()

    # 1時間クールダウン
    if ck in chest_cooldowns and now - chest_cooldowns[ck] < 3600:
        remaining = int(3600 - (now - chest_cooldowns[ck]))
        mins = remaining // 60
        secs = remaining % 60
        await interaction.response.send_message(
            f"⏳ 宝箱のクールダウン中です。あと **{mins}分{secs}秒** お待ちください！",
            ephemeral=True
        )
        return

    chest_cooldowns[ck] = now
    data = load_data(guild_id)
    info = ensure_user_data(data, user_id)

    # 当日獲得上限チェック
    today_earned = info.get("coin_daily_earned", 0)
    if today_earned >= COIN_DAILY_CAP:
        await interaction.response.send_message(
            f"💸 今日のコイン獲得上限（{COIN_DAILY_CAP:,}コイン）に達しています。明日またどうぞ！",
            ephemeral=True
        )
        return

    coin_gain = random.randint(10, 100)
    coin_gain = min(coin_gain, COIN_DAILY_CAP - today_earned)
    info["coins"] = info.get("coins", 0) + coin_gain
    info["coin_daily_earned"] = today_earned + coin_gain
    info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + coin_gain

    # ミッション進捗：チェスト回数
    add_mission_progress(info, "chest_count", 1)

    save_data(guild_id, data)

    embed = discord.Embed(
        title="📦 宝箱を開けた！",
        description=f"{interaction.user.mention} が宝箱を開けました！\n💰 **+{coin_gain:,}コイン** 獲得！",
        color=discord.Color.gold()
    )
    embed.set_footer(text="次の宝箱は1時間後に開けられます")
    await interaction.response.send_message(embed=embed)

# =========================
# 曜日別デイリーミッション定義
# =========================
# weekday(): 0=月 1=火 2=水 3=木 4=金 5=土 6=日
DAILY_MISSIONS = {
    0: {"label": "💬 メッセージを5回送る",       "type": "msg_count",    "goal": 5,   "reward": 100},
    1: {"label": "💬 メッセージを10回送る",      "type": "msg_count",    "goal": 10,  "reward": 200},
    2: {"label": "🎙️ VCに30分滞在する",         "type": "vc_minutes",   "goal": 30,  "reward": 200},
    3: {"label": "⚔️ ボスに300ダメージ与える",   "type": "boss_damage",  "goal": 300, "reward": 500},
    4: {"label": "🎁 チェストを3回開ける",       "type": "chest_count",  "goal": 3,   "reward": 300},
    5: {"label": "📈 投資パックを購入する",      "type": "invest_done",  "goal": 1,   "reward": 500},
    6: {"label": "⭐ XPを500獲得する",           "type": "xp_gained",    "goal": 500, "reward": 300},
}

def get_today_mission():
    """今日の曜日のミッションを返す"""
    weekday = datetime.now(JST).weekday()
    return DAILY_MISSIONS[weekday]

def get_mission_progress(info, mission_type):
    """ミッション進捗を返す"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    progress = info.get("daily_mission_progress", {})
    if progress.get("date") != today:
        return 0
    return progress.get(mission_type, 0)

def add_mission_progress(info, mission_type, amount=1):
    """ミッション進捗を加算する"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    progress = info.get("daily_mission_progress", {})
    if progress.get("date") != today:
        progress = {"date": today}
    progress[mission_type] = progress.get(mission_type, 0) + amount
    info["daily_mission_progress"] = progress

# =========================
# /dailymission（曜日別デイリーミッション確認・受取）
# =========================
@bot.tree.command(name="dailymission", description="今日のデイリーミッションを確認・受け取る")
async def dailymission(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id  = str(interaction.user.id)
    data     = load_data(guild_id)
    info     = ensure_user_data(data, user_id)

    today    = datetime.now(JST).strftime("%Y-%m-%d")
    mission  = get_today_mission()
    m_type   = mission["type"]
    m_goal   = mission["goal"]
    m_reward = mission["reward"]
    m_label  = mission["label"]

    if info.get("daily_mission_claimed") == today:
        await interaction.response.send_message(
            "✅ 今日のデイリーミッションは既に受け取り済みです！明日また来てね。",
            ephemeral=True
        )
        return

    progress = get_mission_progress(info, m_type)
    achieved = progress >= m_goal

    # 木曜ミッション：ボスが不在（討伐済み）なら自動達成
    if m_type == "boss_damage" and not achieved:
        boss = load_boss(guild_id)
        if not boss.get("active"):
            achieved = True
            m_label = "⚔️ ボスに300ダメージ与える（今週のボスは討伐済み！）"

    if not achieved:
        bar_filled = int((min(progress, m_goal) / m_goal) * 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        embed = discord.Embed(
            title="🎯 今日のデイリーミッション",
            description=(
                f"**{m_label}**\n\n"
                f"進捗：`{bar}` {progress:.1f} / {m_goal}\n"
                f"💰 達成報酬：**{m_reward}コイン**\n\n"
                f"⏳ まだ未達成です。頑張ろう！"
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
        return

    today_earned = info.get("coin_daily_earned", 0)
    if today_earned >= COIN_DAILY_CAP:
        await interaction.response.send_message(
            f"💸 今日のコイン獲得上限（{COIN_DAILY_CAP:,}コイン）に達しています。",
            ephemeral=True
        )
        return

    reward_coins = min(m_reward, COIN_DAILY_CAP - today_earned)
    info["coins"] = info.get("coins", 0) + reward_coins
    info["coin_daily_earned"] = today_earned + reward_coins
    info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + reward_coins
    info["daily_mission_claimed"] = today
    save_data(guild_id, data)

    embed = discord.Embed(
        title="🎯 デイリーミッション達成！",
        description=(
            f"**{m_label}**\n\n"
            f"✅ ミッション達成！\n"
            f"💰 **+{reward_coins:,}コイン** 獲得！"
        ),
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

# =========================
# 招待報酬（新規参加時に招待者へ500コイン）
# =========================
@bot.event
async def on_member_join(member):
    guild = member.guild
    gid = guild.id

    # 招待ログから招待者を特定
    try:
        invites_after = await guild.invites()
        # 招待者を特定するため招待履歴と比較（簡易版：招待数が増えたものを使用）
        # ※ 正確な招待追跡にはon_invite_create等との連携が必要
        # ここでは将来拡張用のフックとして残す
        pass
    except discord.Forbidden:
        pass

# =========================
# /setchannel（管理者用：通知チャンネル変更）
# =========================
@bot.tree.command(name="setchannel", description="レベル通知チャンネルを変更（管理者用）")
@discord.app_commands.checks.has_permissions(administrator=True)
async def setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    set_level_channel_id(interaction.guild.id, channel.id)
    embed = discord.Embed(
        title="✅ 通知チャンネルを変更しました",
        description=f"レベル通知チャンネルを {channel.mention} に設定しました！",
        color=discord.Color.green()
    )
    embed.set_footer(text="レベルアップ・ランキング・ボスなどの通知がこのチャンネルに届きます")
    await interaction.response.send_message(embed=embed)

@setchannel.error
async def setchannel_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("このコマンドは管理者のみ使用できます！", ephemeral=True)


# =========================
# /set（管理者用サブコマンドグループ）
# =========================
set_group = discord.app_commands.Group(name="set", description="管理者用設定コマンド")

@set_group.command(name="getchannel", description="XPを獲得できるチャンネルを追加・削除・リセット（管理者用）")
@discord.app_commands.checks.has_permissions(administrator=True)
@discord.app_commands.describe(
    action="add=追加 / remove=削除 / list=一覧 / reset=制限解除（全チャンネル許可に戻す）",
    channel="対象チャンネル（add / remove 時に指定）"
)
@discord.app_commands.choices(action=[
    discord.app_commands.Choice(name="add - チャンネルを追加", value="add"),
    discord.app_commands.Choice(name="remove - チャンネルを削除", value="remove"),
    discord.app_commands.Choice(name="list - 現在の設定を確認", value="list"),
    discord.app_commands.Choice(name="reset - 制限を解除（全チャンネルでXP獲得可）", value="reset"),
])
async def set_getchannel(
    interaction: discord.Interaction,
    action: str,
    channel: discord.TextChannel = None
):
    guild_id = interaction.guild.id

    if action == "add":
        if channel is None:
            await interaction.response.send_message("❌ チャンネルを指定してください。", ephemeral=True)
            return
        add_xp_channel_id(guild_id, channel.id)
        embed = discord.Embed(
            title="✅ XP獲得チャンネルを追加",
            description=f"{channel.mention} をXP獲得チャンネルに追加しました。\n指定チャンネル以外ではXPを獲得できなくなります。",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    elif action == "remove":
        if channel is None:
            await interaction.response.send_message("❌ チャンネルを指定してください。", ephemeral=True)
            return
        success = remove_xp_channel_id(guild_id, channel.id)
        if success:
            embed = discord.Embed(
                title="✅ XP獲得チャンネルを削除",
                description=f"{channel.mention} をXP獲得チャンネルの一覧から削除しました。",
                color=discord.Color.orange()
            )
        else:
            embed = discord.Embed(
                title="⚠️ 未登録のチャンネル",
                description=f"{channel.mention} はXP獲得チャンネルに登録されていません。",
                color=discord.Color.red()
            )
        await interaction.response.send_message(embed=embed)

    elif action == "list":
        xp_channels = get_xp_channel_ids(guild_id)
        if not xp_channels:
            desc = "現在制限なし。**全チャンネル**でXPを獲得できます。"
        else:
            mentions = [f"<#{cid}>" for cid in xp_channels]
            desc = "以下のチャンネルのみXPを獲得できます：\n" + "\n".join(mentions)
        embed = discord.Embed(
            title="📋 XP獲得チャンネル一覧",
            description=desc,
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    elif action == "reset":
        clear_xp_channels(guild_id)
        embed = discord.Embed(
            title="🔓 XP獲得チャンネル制限を解除",
            description="チャンネル制限をリセットしました。\n再び**全チャンネル**でXPを獲得できます。",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

bot.tree.add_command(set_group)


# =========================
# /setuproles（管理者用：ロール・チャンネルの再セットアップ）
# =========================
@bot.tree.command(name="setuproles", description="ボット用ロール・通知チャンネルを再作成（管理者用）")
@discord.app_commands.checks.has_permissions(administrator=True)
async def setuproles(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild

    roles_to_create = [
        {"name": "MEMBER Lite",  "color": discord.Color.from_rgb(153, 153, 153)},
        {"name": "MEMBER",       "color": discord.Color.from_rgb(59,  165,  93)},
        {"name": "CORE",         "color": discord.Color.from_rgb(31,  139,  76)},
        {"name": "SELECT",       "color": discord.Color.from_rgb(78,   93, 148)},
        {"name": "PREMIUM",      "color": discord.Color.from_rgb(255, 168,   0)},
        {"name": "VIP Lite",     "color": discord.Color.from_rgb(163,  73, 164)},
        {"name": "VIP",          "color": discord.Color.from_rgb(113,  54, 138)},
        {"name": "Legend",       "color": discord.Color.from_rgb( 85, 205, 252)},
        {"name": "🥇週間王者",   "color": discord.Color.from_rgb(255, 168,   0)},
        {"name": "🥈週間準王",   "color": discord.Color.from_rgb(153, 153, 153)},
        {"name": "🥉週間三位",   "color": discord.Color.from_rgb(180, 100,  40)},
        {"name": "PHOTO+",       "color": discord.Color.from_rgb(255, 255, 255)},
        {"name": "⚔️ボス討伐者", "color": discord.Color.from_rgb(220,  50,  50)},
    ]

    created_roles = []
    skipped_roles = []

    for role_data in roles_to_create:
        if discord.utils.get(guild.roles, name=role_data["name"]):
            skipped_roles.append(role_data["name"])
            continue
        try:
            await guild.create_role(
                name=role_data["name"],
                color=role_data["color"],
                reason="setuprolesコマンドによる再セットアップ"
            )
            created_roles.append(role_data["name"])
            await asyncio.sleep(0.5)
        except discord.Forbidden:
            pass

    # ロール位置の整理
    try:
        role_order = [
            "🥇週間王者", "🥈週間準王", "🥉週間三位",
            "Legend", "VIP", "VIP Lite", "PREMIUM", "SELECT",
            "CORE", "MEMBER", "MEMBER Lite",
            "⚔️ボス討伐者", "PHOTO+"
        ]
        bot_role = guild.me.top_role
        max_pos = bot_role.position - 1
        positions = {}
        for i, role_name in enumerate(role_order):
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                positions[role] = max_pos - i
        if positions:
            await guild.edit_role_positions(positions=positions)
    except (discord.Forbidden, Exception):
        pass

    # 通知チャンネルの確認・作成
    channel_msg = ""
    notify_channel = None
    existing = discord.utils.get(guild.text_channels, name="レベル通知")
    if existing:
        channel_msg = f"📢 通知チャンネルは既に存在します: {existing.mention}"
        if not get_level_channel_id(guild.id):
            set_level_channel_id(guild.id, existing.id)
    else:
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False, read_messages=True),
                guild.me: discord.PermissionOverwrite(send_messages=True, read_messages=True)
            }
            notify_channel = await guild.create_text_channel(
                name="レベル通知",
                overwrites=overwrites,
                reason="setuprolesコマンドによる再セットアップ"
            )
            set_level_channel_id(guild.id, notify_channel.id)
            channel_msg = f"📢 通知チャンネルを新規作成しました: {notify_channel.mention}"
        except discord.Forbidden:
            channel_msg = "⚠️ 通知チャンネルの作成に失敗しました（権限不足）"

    # 結果レポート
    desc = ""
    if created_roles:
        desc += f"✅ **新規作成したロール（{len(created_roles)}個）**\n"
        desc += "\n".join(f"　・{r}" for r in created_roles) + "\n\n"
    if skipped_roles:
        desc += f"⏭️ **既に存在するロール（{len(skipped_roles)}個）**\n"
        desc += "\n".join(f"　・{r}" for r in skipped_roles) + "\n\n"
    desc += channel_msg

    embed = discord.Embed(
        title="🔧 セットアップ結果",
        description=desc,
        color=discord.Color.green() if created_roles else discord.Color.blue()
    )
    await interaction.followup.send(embed=embed, ephemeral=True)

@setuproles.error
async def setuproles_error(interaction: discord.Interaction, error):
    if isinstance(error, discord.app_commands.MissingPermissions):
        await interaction.response.send_message("このコマンドは管理者のみ使用できます！", ephemeral=True)

# =========================
# =========================
# /eventboss コマンド群
# =========================
# =========================
# /allservereventboss（bot管理者専用・サーバーリンクボス）
# =========================
allservereventboss_group = discord.app_commands.Group(
    name="allservereventboss",
    description="サーバーリンクボス管理（bot管理者専用）"
)

@allservereventboss_group.command(name="start", description="サーバーリンクボスを召喚（bot管理者専用）")
@discord.app_commands.describe(
    name="ボスの名前",
    hp="ボスのHP",
    days="開催期間（日）",
    boost="討伐後のXPブースト倍率",
    boost_days="討伐後のXPブースト期間（日）※省略時はdaysと同じ",
    reward_xp="報酬受取条件：討伐期間中に必要な最低ダメージ量（0=全員対象）",
)
async def allservereventboss_start(
    interaction: discord.Interaction,
    name: str = "大魔王",
    hp: int = 500000,
    days: int = 7,
    boost: int = 3,
    boost_days: int = None,
    reward_xp: int = 0,
):
    if not is_bot_admin(interaction.user.id):
        await interaction.response.send_message("❌ このコマンドはbot管理者のみ使用できます。", ephemeral=True)
        return

    global_boss = load_global_event_boss()
    if global_boss.get("active"):
        await interaction.response.send_message("⚠️ 既にサーバーリンクボスが出現中です！", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    actual_boost_days = boost_days if boost_days is not None else days

    new_boss = {
        "active":           True,
        "name":             name,
        "hp":               hp,
        "max_hp":           hp,
        "damage":           {},
        "boost_days":       actual_boost_days,
        "boost_multiplier": boost,
        "end_at":           time.time() + days * 24 * 60 * 60,
        "reward_xp":        reward_xp,
    }
    save_global_event_boss(new_boss)

    # 全サーバーに出現通知
    success = 0
    for guild in bot.guilds:
        ch_id = get_level_channel_id(guild.id)
        notify_channel = guild.get_channel(ch_id) if ch_id else None
        if notify_channel:
            embed = discord.Embed(
                title=f"🌐🚨 サーバーリンクボスイベント！【{name}】出現！",
                description=(
                    f"**全サーバー合同で倒すボス**が現れた！\n\n"
                    f"メッセージを送るだけで自動攻撃！\n"
                    f"どのサーバーのメンバーでも参加できます！\n\n"
                    f"討伐成功で全サーバーのXPが **{boost}倍**（{actual_boost_days}日間）になります！"
                ),
                color=discord.Color.from_rgb(255, 50, 50)
            )
            embed.add_field(name="❤️ HP",       value=f"{hp:,}")
            embed.add_field(name="📅 開催期間",  value=f"{days}日間")
            reward_text = f"XP **{boost}倍**（{actual_boost_days}日間） + `{EVENT_BOSS_CLEAR_ROLE}` ロール"
            if reward_xp > 0:
                reward_text += f"\n⚠️ 受取条件：討伐期間中に **{reward_xp:,}ダメージ以上** 与えたメンバーのみ"
            embed.add_field(name="🎁 報酬", value=reward_text, inline=False)
            embed.set_footer(text="全サーバーで力を合わせて倒せ！")
            try:
                await notify_channel.send("@everyone", embed=embed)
                success += 1
            except (discord.Forbidden, discord.HTTPException):
                pass

    await interaction.followup.send(
        f"✅ サーバーリンクボス【{name}】を召喚しました！\n"
        f"HP: {hp:,} ／ 開催期間: {days}日 ／ ブースト: {boost}倍 × {actual_boost_days}日間\n"
        f"通知送信：{success}サーバー",
        ephemeral=True
    )

bot.tree.add_command(allservereventboss_group)

# =========================
# サーバー参加時：ロール＆チャンネル自動作成
# =========================
@bot.event
async def on_guild_join(guild):
    roles_to_create = [
        {"name": "MEMBER Lite",  "color": discord.Color.from_rgb(153, 153, 153)},
        {"name": "MEMBER",       "color": discord.Color.from_rgb(59,  165,  93)},
        {"name": "CORE",         "color": discord.Color.from_rgb(31,  139,  76)},
        {"name": "SELECT",       "color": discord.Color.from_rgb(78,   93, 148)},
        {"name": "PREMIUM",      "color": discord.Color.from_rgb(255, 168,   0)},
        {"name": "VIP Lite",     "color": discord.Color.from_rgb(163,  73, 164)},
        {"name": "VIP",          "color": discord.Color.from_rgb(113,  54, 138)},
        {"name": "Legend",       "color": discord.Color.from_rgb( 85, 205, 252)},  # ダイヤモンドブルー,
        {"name": "🥇週間王者",   "color": discord.Color.from_rgb(255, 168,   0)},
        {"name": "🥈週間準王",   "color": discord.Color.from_rgb(153, 153, 153)},
        {"name": "🥉週間三位",   "color": discord.Color.from_rgb(180, 100,  40)},
        {"name": "PHOTO+",       "color": discord.Color.from_rgb(255, 255, 255)},
        {"name": "⚔️ボス討伐者", "color": discord.Color.from_rgb(220,  50,  50)},
        {"name": "👑BOSS VIP",   "color": discord.Color.from_rgb(255, 215,   0)},  # イベント限定
    ]

    created_roles = []
    for role_data in roles_to_create:
        if discord.utils.get(guild.roles, name=role_data["name"]):
            continue
        try:
            await guild.create_role(
                name=role_data["name"],
                color=role_data["color"],
                reason="Bot自動セットアップ"
            )
            created_roles.append(role_data["name"])
            await asyncio.sleep(0.5)
        except discord.Forbidden:
            pass

    # ロールの位置を整理（Botロールを上に、ランク・週間ロールをその下に）
    try:
        # 並び順（上から順に）
        role_order = [
            "🥇週間王者", "🥈週間準王", "🥉週間三位",
            "Legend", "VIP", "VIP Lite", "PREMIUM", "SELECT",
            "CORE", "MEMBER", "MEMBER Lite",
            "⚔️ボス討伐者", "PHOTO+"
        ]

        # Botのロールを取得（一番上に移動）
        bot_role = guild.me.top_role
        max_pos = bot_role.position - 1  # Botロールの1つ下から配置

        positions = {}
        for i, role_name in enumerate(role_order):
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                positions[role] = max_pos - i

        if positions:
            await guild.edit_role_positions(positions=positions)
    except discord.Forbidden:
        pass
    except Exception:
        pass

    notify_channel = None
    existing = discord.utils.get(guild.text_channels, name="レベル通知")
    if existing:
        notify_channel = existing
    else:
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False, read_messages=True),
                guild.me: discord.PermissionOverwrite(send_messages=True, read_messages=True)
            }
            notify_channel = await guild.create_text_channel(
                name="レベル通知",
                overwrites=overwrites,
                reason="Bot自動セットアップ"
            )
        except discord.Forbidden:
            pass

    # チャンネルIDをconfig.jsonに保存
    if notify_channel:
        set_level_channel_id(guild.id, notify_channel.id)

        embed = discord.Embed(
            title="👋 セットアップ完了！",
            description=(
                "レベルBotの準備ができました！\n\n"
                "**作成されたもの**\n"
                f"📢 通知チャンネル: {notify_channel.mention}\n"
                f"🎭 ロール: {len(created_roles)}個作成\n\n"
                "詳しい使い方は **#bot説明** チャンネルをご確認ください！"
            ),
            color=discord.Color.green()
        )
        await notify_channel.send(embed=embed)

    # ===== bot説明チャンネルを作成 =====
    desc_channel = None
    existing_desc = discord.utils.get(guild.text_channels, name="bot説明")
    if existing_desc:
        desc_channel = existing_desc
    else:
        try:
            overwrites_desc = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False, read_messages=True),
                guild.me: discord.PermissionOverwrite(send_messages=True, read_messages=True)
            }
            desc_channel = await guild.create_text_channel(
                name="bot説明",
                overwrites=overwrites_desc,
                reason="Bot自動セットアップ"
            )
        except discord.Forbidden:
            pass

    if desc_channel:
        embed1 = discord.Embed(
            title="🤖 レベルBotへようこそ！",
            description=(
                "このBotはDiscordサーバーにレベル・XP・ランキング・ボス討伐などの\n"
                "ゲーミフィケーション機能を追加します！\n\n"
                "**📌 基本的な仕組み**\n"
                "・メッセージを送ると**XP**が貯まります\n"
                "　└ 10秒以内に3回以上送るとXP無効（スパム対策）\n"
                "・VCに2人以上いると**30秒ごとにXP**が貯まります\n"
                "　└ ミュート中: +2XP　／　発言中: +15XP\n"
                "・XPが一定量貯まると**レベルアップ**します\n"
                "・レベルに応じて**ランクロール**が自動付与されます\n\n"
                "**🔥 XPブースト**\n"
                "・毎日ランダムな時間に**2〜3倍ブースト**が1時間発動！\n"
                "・ボス討伐後は**討伐者にXP2倍**の個人バフが付与されます\n\n"
                "**✨ クリティカル**\n"
                "XP獲得時に低確率でクリティカルが発生しXPが大幅アップ！\n"
                "✨ミニCT×5 ／ 🔥CT×10 ／ ⚡超CT×25 ／ 💥超+CT×50"
            ),
            color=discord.Color.blue()
        )
        await desc_channel.send(embed=embed1)

        embed2 = discord.Embed(
            title="🎭 ランクロール一覧",
            description=(
                "レベルに応じて自動でロールが変わります！\n\n"
                "Lv1〜9：MEMBER Lite\n"
                "Lv10〜29：MEMBER\n"
                "Lv30〜49：CORE\n"
                "Lv50〜74：SELECT\n"
                "Lv75〜99：PREMIUM\n"
                "Lv100〜199：VIP Lite\n"
                "Lv200〜499：VIP\n"
                "Lv500〜：💎 Legend\n\n"
                "🌟 **Lv3達成で PHOTO+ ロールを永久取得！**\n"
                "🥇 **週間TOP3には週間ロールを付与！**\n"
                "⚔️ **ボス討伐ごとに LV〇〇ボス討伐者 ロールを付与！（月曜リセット）**\n"
                "👑 **イベントボス討伐で 👑BOSS VIP ロールを付与！**"
            ),
            color=discord.Color.gold()
        )
        await desc_channel.send(embed=embed2)

        embed3 = discord.Embed(
            title="🎁 デイリーボーナス & 💰 コインシステム",
            description=(
                "**🎁 デイリーボーナス（1日1回）**\n"
                "毎日最初のメッセージまたはVC入室でXP & コインを獲得！\n"
                "1日目:+100XP / 2日目:+200XP / 3日目:+300XP\n"
                "4日目:+500XP / 5日目以降:+1000XP（MAX）\n"
                "連続ログインでコインも増加！（100+連続日数×20、上限500）\n\n"
                "**💰 コイン獲得方法**\n"
                "・レベルアップ: +100〜500コイン\n"
                "・週間ランキング: 1位3000 / 2位2000 / 3位1000コイン\n"
                "・週間1000XP達成ボーナス: +500コイン\n"
                "・ボス討伐: ダメージ×0.1コイン\n"
                "・宝箱（/chest）: 10〜100コイン（1時間CD）\n"
                "・デイリーミッション（/dailymission）: 100〜500コイン（曜日別）\n\n"
                "※ 1日の獲得上限は **1,500コイン** です"
            ),
            color=discord.Color.green()
        )
        await desc_channel.send(embed=embed3)

        embed4 = discord.Embed(
            title="🛒 ショップアイテム一覧",
            description=(
                "コインを使ってアイテムを購入！（`/shop` で確認・`/buy` で購入）\n\n"
                "**⚙️ 便利系**\n"
                "・**XPブースト（小）** … 1,500コイン｜XP×1.5（10分）\n"
                "・**XPブースト（中）** … 2,000コイン｜XP×2.0（10分）\n"
                "・**デイリー強化** … 1,000コイン｜デイリー報酬×1.5（24時間）\n"
                "・**🛡️ XP減衰ガード** … 2,500コイン｜その日のXP減衰を無効化\n"
                "・**🔰 ランクダウン防止シールド** … 5,000コイン｜次の1回のランクダウンを防ぐ\n\n"
                "**⚔️ 戦闘系**\n"
                "・**攻撃力アップ** … 1,000コイン｜ボスダメージ×1.2（60分）\n"
                "・**クリティカル強化** … 5,000コイン｜クリティカル率アップ（15分）\n"
                "・**ボス特効** … 2,000コイン｜ボスダメージ×1.3（15分）\n\n"
                "**🎲 ギャンブル系**\n"
                "・**🎁 ミステリーボックス** … 3,000コイン｜`/openbox` で使用\n"
                "・**📈 投資パック** … 3,000コイン〜｜`/invest` で使用（500コイン単位）\n\n"
                "**👑 プレミアム**\n"
                "・**👑 ROYAL PASS** … 50,000コイン｜XP+20%・デイリー+50%・特別ロール（7日間）"
            ),
            color=discord.Color.purple()
        )
        await desc_channel.send(embed=embed4)

        embed5 = discord.Embed(
            title="👹 ボスシステム",
            description=(
                "**週ボス**\n"
                "・毎週月曜6時に出現！ 初期HP: 30,000\n"
                "・メッセージ or VC参加で自動攻撃！\n"
                "・討伐成功: LV〇〇ボス討伐者ロール + XP2倍ブースト\n"
                "・討伐失敗: 残りHP + 最大HPの20%回復して翌週再出現\n"
                "・HP報告: 毎日8時・16時・0時\n\n"
                "**👑 イベントボス**\n"
                "・通常ボスを5回クリアするたびに自動出現！\n"
                "・討伐成功: 👑BOSS VIPロール + XP3倍ブースト（7日間）\n"
                "・MVPには特別称号メッセージ！\n\n"
                "**🌐 サーバーリンクボス**\n"
                "・全サーバー合同で挑む特別ボス！\n"
                "・メッセージ or VC参加で全サーバーのダメージが合算！\n"
                "・討伐成功: 条件達成者に個人XPブースト + 👑BOSS VIPロール"
            ),
            color=discord.Color.red()
        )
        await desc_channel.send(embed=embed5)

        embed6 = discord.Embed(
            title="📋 コマンド一覧",
            color=discord.Color.blurple()
        )
        embed6.add_field(
            name="👤 一般コマンド",
            value=(
                "`/rank` - 自分のレベル・XPバー確認\n"
                "`/myxp` - レベル・XP・連続ログイン詳細\n"
                "`/top` - XPランキングTOP10\n"
                "`/weeklynote` - 今週の活動レポート\n"
                "`/bosstats` - ボス討伐統計・全ボス状況確認\n"
                "`/serverranking` - 全サーバー週間XPランキング\n"
                "`/rates` - 現在のXPブースト倍率確認\n"
                "`/levelstats` - サーバーのレベル分布\n"
                "`/peakhours` - 時間帯別アクティブ分析\n"
                "`/servercoinsranking` - 全サーバーコインランキング"
            ),
            inline=False
        )
        embed6.add_field(
            name="💰 コイン・ショップ",
            value=(
                "`/coins` - 所持コイン確認\n"
                "`/buffs` - 有効なバフ確認\n"
                "`/shop` - ショップ一覧表示\n"
                "`/buy <item_id>` - アイテム購入\n"
                "`/openbox` - ミステリーボックスを開ける\n"
                "`/invest` - 投資パック購入\n"
                "`/investstatus` - 投資状況確認\n"
                "`/claiminvest` - 投資回収\n"
                "`/buyroyal` - ROYAL PASS購入\n"
                "`/chest` - 宝箱を開ける（1時間CD）\n"
                "`/dailymission` - デイリーミッション確認・受け取り\n"
                "`/shopstats` - 人気アイテムTOP5"
            ),
            inline=False
        )
        embed6.add_field(
            name="🔧 管理者コマンド",
            value=(
                "`/setchannel` - 通知チャンネル変更\n"
                "`/set getchannel` - XP獲得チャンネルの設定\n"
                "`/setuproles` - ロール・チャンネル再セットアップ\n"
                "`/userdata @ユーザー` - ユーザーデータ確認\n"
                "`/alldata` - 全データCSV出力"
            ),
            inline=False
        )
        await desc_channel.send(embed=embed6)



# =========================
# /announce（全サーバー一斉アナウンス）
# =========================
@bot.tree.command(name="announce", description="全サーバーにアナウンスを送信（Bot管理者専用）")
@discord.app_commands.describe(
    message="送信するメッセージ内容",
    mention_type="メンション方法",
    target="送信先チャンネル",
)
@discord.app_commands.choices(
    mention_type=[
        discord.app_commands.Choice(name="通常メッセージ（メンションなし）", value="none"),
        discord.app_commands.Choice(name="@here メンション",                value="here"),
        discord.app_commands.Choice(name="@everyone メンション",            value="everyone"),
    ],
    target=[
        discord.app_commands.Choice(name="通知チャンネル",         value="notify"),
        discord.app_commands.Choice(name="bot説明チャンネル",      value="desc"),
        discord.app_commands.Choice(name="両方",                   value="both"),
    ]
)
async def announce(
    interaction: discord.Interaction,
    message: str,
    mention_type: str = "none",
    target: str = "notify",
):
    if not is_bot_admin(interaction.user.id):
        await interaction.response.send_message(
            "❌ このコマンドはBot管理者のみ使用できます！",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    embed = discord.Embed(
        title="📢 お知らせ",
        description=message,
        color=discord.Color.orange()
    )
    embed.set_footer(text=f"送信者: {interaction.user.display_name}")
    embed.timestamp = datetime.now(JST)

    if mention_type == "everyone":
        prefix = "@everyone"
    elif mention_type == "here":
        prefix = "@here"
    else:
        prefix = None

    success = 0
    failed  = 0

    for guild in bot.guilds:
        channels_to_send = []

        if target in ("notify", "both"):
            ch_id = get_level_channel_id(guild.id)
            ch = guild.get_channel(ch_id) if ch_id else None
            if ch:
                channels_to_send.append(ch)

        if target in ("desc", "both"):
            desc_ch = discord.utils.get(guild.text_channels, name="bot説明")
            if desc_ch:
                channels_to_send.append(desc_ch)

        for ch in channels_to_send:
            try:
                await ch.send(content=prefix, embed=embed)
                success += 1
            except Exception:
                failed += 1

    await interaction.followup.send(
        f"✅ アナウンス送信完了！\n"
        f"・メンション：{mention_type}\n"
        f"・成功：{success}チャンネル\n"
        f"・失敗：{failed}チャンネル",
        ephemeral=True
    )

# =========================
# 全サーバー対抗戦（週間XP合計ランキング）
# =========================
_server_ranking_announced = {}  # { date_str: True }

def get_server_weekly_xp(guild):
    """サーバーの今週の総XPを返す"""
    data = load_data(guild.id)
    total = sum(
        info.get("weekly_xp", 0)
        for uid, info in data.items()
        if uid != LAST_DECAY_KEY
    )
    active_members = sum(
        1 for uid, info in data.items()
        if uid != LAST_DECAY_KEY and info.get("weekly_xp", 0) > 0
    )
    return total, active_members

def build_server_ranking_embed(bot, title="🌐 全サーバー週間XPランキング", color=discord.Color.gold(), current_guild=None):
    """全サーバーのランキングEmbedを生成。current_guild指定時は自サーバーの順位・差分も表示。"""
    results = []
    for guild in bot.guilds:
        total_xp, active = get_server_weekly_xp(guild)
        results.append((guild, total_xp, active))

    results.sort(key=lambda x: x[1], reverse=True)

    medals = ["🥇", "🥈", "🥉"]
    desc = ""
    for i, (guild, total_xp, active) in enumerate(results, start=1):
        medal = medals[i - 1] if i <= 3 else f"`{i}.`"

        # 次順位との差分
        if i == 1:
            diff_text = "👑 現在首位！"
        else:
            prev_xp   = results[i - 2][1]
            diff      = prev_xp - total_xp
            diff_text = f"次の順位まで **{diff:,} XP**！"

        desc += f"{medal} **{guild.name}**\n　総XP：**{total_xp:,}** ／ {active}人 ／ {diff_text}\n"

    if not desc:
        desc = "データがありません。"

    embed = discord.Embed(title=title, description=desc, color=color)

    # 自サーバーがTOP10圏外の場合に追記
    if current_guild:
        own_rank  = next((i + 1 for i, (g, _, _) in enumerate(results) if g.id == current_guild.id), None)
        own_xp    = next((xp for g, xp, _ in results if g.id == current_guild.id), 0)
        own_active = next((a for g, _, a in results if g.id == current_guild.id), 0)

        if own_rank and own_rank > 10:
            if own_rank == 1:
                own_diff_text = "👑 現在首位！"
            else:
                prev_xp   = results[own_rank - 2][1]
                diff      = prev_xp - own_xp
                own_diff_text = f"次の順位まで **{diff:,} XP**！"

            embed.add_field(
                name=f"📍 {current_guild.name} の順位",
                value=(
                    f"`{own_rank}位` ／ 総XP：**{own_xp:,}** ／ {own_active}人\n"
                    f"{own_diff_text}"
                ),
                inline=False
            )

    embed.set_footer(text="集計期間：今週（月曜リセット）")
    return embed, results

@tasks.loop(minutes=1)
async def server_ranking_task():
    await bot.wait_until_ready()
    now = datetime.now(JST)

    # 毎日15:00に発表
    if not (now.hour == 15 and now.minute == 0):
        return

    date_key = now.strftime("%Y-%m-%d")
    if _server_ranking_announced.get(date_key):
        return
    _server_ranking_announced[date_key] = True

    embed, results = build_server_ranking_embed(
        bot,
        title="🏆 本日の全サーバー対抗戦 戦況レポート！",
        color=discord.Color.gold()
    )
    now_str = now.strftime("%Y/%m/%d %H:%M")
    embed.set_footer(text=f"集計時刻：{now_str} JST ／ 集計期間：今週（月曜リセット）")

    for guild in bot.guilds:
        ch_id = get_level_channel_id(guild.id)
        notify_channel = guild.get_channel(ch_id) if ch_id else None
        if notify_channel:
            embed, results = build_server_ranking_embed(
                bot,
                title="🏆 本日の全サーバー対抗戦 戦況レポート！",
                color=discord.Color.gold(),
                current_guild=guild
            )
            now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
            embed.set_footer(text=f"集計時刻：{now_str} JST ／ 集計期間：今週（月曜リセット）")
            top_msg = f"\n🎉 現在の首位は **{results[0][0].name}**！ 総XP **{results[0][1]:,}**" if results else ""
            try:
                await notify_channel.send(content=top_msg if top_msg else None, embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

@bot.tree.command(name="serverranking", description="全サーバーの今週のXPランキングを表示")
async def serverranking(interaction: discord.Interaction):
    embed, _ = build_server_ranking_embed(bot, current_guild=interaction.guild)
    await interaction.response.send_message(embed=embed)


# =========================
# /startbattle（bot管理者専用：対抗戦を即時リセット＆スタート）
# =========================
@bot.tree.command(name="startbattle", description="【bot管理者専用】全サーバーの週間XPをリセットして対抗戦を即時スタート")
async def startbattle(interaction: discord.Interaction):
    if not is_bot_admin(interaction.user.id):
        await interaction.response.send_message("❌ このコマンドはbot管理者のみ使用できます。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    reset_count = 0
    for guild in bot.guilds:
        data = load_data(guild.id)
        for uid in data:
            if uid == LAST_DECAY_KEY:
                continue
            data[uid]["weekly_xp"] = 0
            data[uid]["weekly_chat_xp"] = 0
            data[uid]["weekly_vc_xp"] = 0
            data[uid]["weekly_active_days"] = []
            data[uid]["weekly_coins_spent"] = 0
        save_data(guild.id, data)
        reset_count += 1

    # 発表済みフラグもリセット（今週の水曜レポートを再送できるように）
    _server_ranking_announced.clear()

    now_str = datetime.now(JST).strftime("%Y/%m/%d %H:%M")

    # 全サーバーの通知チャンネルに対抗戦スタートを告知
    announce_embed = discord.Embed(
        title="⚔️ 全サーバー対抗戦 スタート！",
        description=(
            "全サーバーの週間XPがリセットされました！\n\n"
            "今ここから新しいバトルが始まります🔥\n"
            "今週の戦況レポートは **毎日15:00** に発表されます！\n\n"
            f"`/serverranking` でいつでも現在の順位を確認できます。"
        ),
        color=discord.Color.red()
    )
    announce_embed.set_footer(text=f"スタート時刻：{now_str} JST")

    for guild in bot.guilds:
        ch_id = get_level_channel_id(guild.id)
        notify_channel = guild.get_channel(ch_id) if ch_id else None
        if notify_channel:
            try:
                await notify_channel.send(embed=announce_embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    await interaction.followup.send(
        f"✅ {reset_count}サーバーのXPをリセットし、対抗戦をスタートしました！",
        ephemeral=True
    )


# =========================
# ミステリーボックス抽選関数
# =========================
def draw_mystery_box():
    r = random.random()

    if r < 0.25:  # ハズレ 25%
        lose_type = random.choice(["small_coins", "big_lose", "curse", "msg1", "msg2"])
        return "lose", lose_type

    if r < 0.95:  # 通常 70%
        reward_type = random.choice(["xp_boost", "attack_up", "coins", "login_bonus_2x"])
        if reward_type == "xp_boost":
            return "normal", {"type": "xp_boost", "duration": 30 * 60, "value": 2.0}
        elif reward_type == "attack_up":
            return "normal", {"type": "attack_up", "duration": 15 * 60, "value": 1.2}
        elif reward_type == "coins":
            return "normal", {"type": "coins", "amount": random.randint(300, 800)}
        else:
            return "normal", {"type": "login_bonus_2x"}

    # レア 5%
    rare_type = random.choice(["coins", "boss_slayer", "rare_role"])
    if rare_type == "coins":
        return "rare", {"type": "coins", "amount": 5000}
    elif rare_type == "boss_slayer":
        return "rare", {"type": "boss_slayer", "duration": 30 * 60, "value": 1.5}
    else:
        return "rare", {"type": "rare_role"}

# =========================
# /openbox（ミステリーボックス購入＆使用）
# =========================
_openbox_cooldowns = {}

@bot.tree.command(name="openbox", description="🎁 ミステリーボックスを購入して開ける（3000コイン）")
async def openbox(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id  = str(interaction.user.id)
    ck = f"{guild_id}:{user_id}"
    now = time.time()

    if ck in _openbox_cooldowns and now - _openbox_cooldowns[ck] < 30:
        remain = int(30 - (now - _openbox_cooldowns[ck]))
        await interaction.response.send_message(f"⏳ あと **{remain}秒** 待ってから開けてください！", ephemeral=True)
        return

    data  = load_data(guild_id)
    info  = ensure_user_data(data, user_id)
    price = SHOP_ITEMS["mystery_box"]["price"]

    if not spend_coins(data, user_id, price, "buy_mystery_box"):
        await interaction.response.send_message(
            f"コインが足りません。\n必要: **{price:,}コイン** ／ 所持: **{info.get('coins',0):,}コイン**", ephemeral=True
        )
        return

    _openbox_cooldowns[ck] = now
    info["weekly_coins_spent"] = info.get("weekly_coins_spent", 0) + price
    shop_log = load_shop_log(guild_id)
    week_key = datetime.now(JST).strftime("%Y-W%W")
    shop_log.setdefault(week_key, {})
    shop_log[week_key]["mystery_box"] = shop_log[week_key].get("mystery_box", 0) + 1
    save_shop_log(guild_id, shop_log)

    rarity, reward = draw_mystery_box()

    # ハズレ処理
    if rarity == "lose":
        LOSE_PATTERNS = {
            "small_coins": ("空箱だった…\n\nせめてもの慰めに 💰 **+50コイン** をどうぞ。",    50),
            "big_lose":    ("完全な空箱だった…！\n\n慰めに 💰 **+100コイン** をどうぞ。",    100),
            "curse":       ("💀 **呪いのボックス！！**\n\nXPが **-50** 削られてしまった…！", 0),
            "msg1":        ("ゴロゴロ…カラン…\n\n**何も入っていなかった。** 💰 **+100コイン**", 100),
            "msg2":        ("箱を開けたら説明書だけ入ってた。\n\n💰 **+100コイン** で許して。", 100),
        }
        msg, coin_back = LOSE_PATTERNS[reward]

        if reward == "curse":
            info["xp"] = max(0, info.get("xp", 0) - 50)
        else:
            info["coins"] = info.get("coins", 0) + coin_back
            info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + coin_back

        save_data(guild_id, data)
        embed = discord.Embed(
            title="📦 ミステリーボックスを開けた！",
            description=msg,
            color=discord.Color.greyple()
        )
        embed.set_footer(text="次こそはレアが出るかも…？")
        await interaction.response.send_message(embed=embed)
        return

    # 通常・レア報酬付与
    embed_color = discord.Color.gold() if rarity == "normal" else discord.Color.from_rgb(255, 50, 200)
    reward_text = ""

    if reward["type"] == "coins":
        amount = reward["amount"]
        info["coins"] = info.get("coins", 0) + amount
        info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + amount
        reward_text = f"💰 **+{amount:,}コイン** 獲得！"
    elif reward["type"] == "login_bonus_2x":
        info["login_bonus_2x"] = True
        reward_text = "🎁 **翌日のログインボーナス2倍チケット** 獲得！\n次回ログイン時に自動適用されます。"
    elif reward["type"] == "xp_boost":
        add_timed_buff(info, "xp_multiplier", reward["value"], reward["duration"], "mystery_xp_boost")
        reward_text = f"⚡ **XPブースト {reward['value']}倍**（30分）獲得！"
    elif reward["type"] == "attack_up":
        add_timed_buff(info, "damage_multiplier", reward["value"], reward["duration"], "mystery_attack")
        reward_text = f"⚔️ **攻撃力アップ {reward['value']}倍**（15分）獲得！"
    elif reward["type"] == "boss_slayer":
        add_timed_buff(info, "boss_damage_multiplier", reward["value"], reward["duration"], "mystery_boss")
        reward_text = f"🗡️ **ボス特効 {reward['value']}倍**（30分）獲得！"
    elif reward["type"] == "rare_role":
        role_name = "🎁 ミステリー当選者"
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if not role:
            try:
                role = await interaction.guild.create_role(
                    name=role_name, color=discord.Color.from_rgb(255, 215, 0), reason="ミステリーボックス レア称号"
                )
            except discord.Forbidden:
                role = None
        if role:
            try:
                await interaction.user.add_roles(role)
            except discord.Forbidden:
                pass
        reward_text = f"👑 **レア称号「{role_name}」** を獲得！"

    save_data(guild_id, data)

    title  = "🌟✨ レア報酬！！ ✨🌟" if rarity == "rare" else "📦 ミステリーボックスを開けた！"
    prefix = "🎊 おめでとうございます！！\n\n" if rarity == "rare" else ""
    embed  = discord.Embed(title=title, description=f"{prefix}{reward_text}", color=embed_color)
    await interaction.response.send_message(embed=embed)


# =========================
# 投資パック定数・抽選
# =========================
INVEST_TABLE = [
    (0.5, 0.40, "大暴落…",       "📉"),
    (1.0, 0.35, "元本割れなし",  "📊"),
    (1.5, 0.20, "安定した利益！", "📊"),
    (3.0, 0.05, "爆益！！",      "📈"),
]

def draw_investment():
    r = random.random()
    cumulative = 0.0
    for multiplier, prob, label, emoji in INVEST_TABLE:
        cumulative += prob
        if r < cumulative:
            return multiplier, label, emoji
    return 1.0, "元本割れなし", "📊"

# =========================
# /invest（投資パック購入）
# =========================
INVEST_MIN    = 500
INVEST_MAX    = 50000
INVEST_STEP   = 500
INVEST_DURATION = 24 * 60 * 60

@bot.tree.command(name="invest", description="📈 コインを投資！500コイン単位で設定可（24時間後に /claiminvest で回収）")
@discord.app_commands.describe(amount="投資額（500コイン単位・500〜50,000コイン）")
async def invest(interaction: discord.Interaction, amount: int = 3000):
    guild_id = interaction.guild.id
    user_id  = str(interaction.user.id)
    data = load_data(guild_id)
    info = ensure_user_data(data, user_id)

    # バリデーション
    if amount % INVEST_STEP != 0:
        await interaction.response.send_message(
            f"❌ 投資額は **{INVEST_STEP:,}コイン単位** で指定してください。\n例：500・1000・3000・10000",
            ephemeral=True
        )
        return
    if amount < INVEST_MIN or amount > INVEST_MAX:
        await interaction.response.send_message(
            f"❌ 投資額は **{INVEST_MIN:,}〜{INVEST_MAX:,}コイン** の範囲で指定してください。",
            ephemeral=True
        )
        return

    inv = info.get("investment")
    if inv:
        claim_at = inv["invested_at"] + INVEST_DURATION
        if time.time() < claim_at:
            remain = int(claim_at - time.time())
            h, m = divmod(remain // 60, 60)
            await interaction.response.send_message(
                f"📊 現在投資中です。回収可能まで残り **{h}時間{m}分**\n`/claiminvest` で回収してください。", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "📊 前回の投資がまだ回収されていません。\n`/claiminvest` で回収してからもう一度どうぞ！", ephemeral=True
            )
        return

    if not spend_coins(data, user_id, amount, "buy_investment_pack"):
        await interaction.response.send_message(
            f"コインが足りません。\n必要: **{amount:,}コイン** ／ 所持: **{info.get('coins',0):,}コイン**", ephemeral=True
        )
        return

    info["weekly_coins_spent"] = info.get("weekly_coins_spent", 0) + amount
    info["investment"] = {"amount": amount, "invested_at": time.time()}

    # ミッション進捗：投資完了
    add_mission_progress(info, "invest_done", 1)
    shop_log = load_shop_log(guild_id)
    week_key = datetime.now(JST).strftime("%Y-W%W")
    shop_log.setdefault(week_key, {})
    shop_log[week_key]["investment_pack"] = shop_log[week_key].get("investment_pack", 0) + 1
    save_shop_log(guild_id, shop_log)
    save_data(guild_id, data)

    embed = discord.Embed(
        title="📈 投資完了！",
        description=(
            f"**{amount:,}コイン** を投資しました！\n\n"
            f"24時間後に `/claiminvest` で結果を確認してください。\n"
            f"運命は…神のみぞ知る🎲"
        ),
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"投資額：{amount:,}コイン ／ 期待値：約×1.025")
    await interaction.response.send_message(embed=embed)

# =========================
# /investstatus（投資状況確認）
# =========================
@bot.tree.command(name="investstatus", description="現在の投資状況を確認する")
async def investstatus(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id  = str(interaction.user.id)
    data = load_data(guild_id)
    info = ensure_user_data(data, user_id)
    inv  = info.get("investment")

    if not inv:
        await interaction.response.send_message(
            "📊 投資中のパックはありません。`/invest` で投資を始めましょう！", ephemeral=True
        )
        return

    claim_at  = inv["invested_at"] + INVEST_DURATION
    claimable = time.time() >= claim_at

    if claimable:
        status_text = "✅ **回収可能です！** `/claiminvest` で回収してください。"
        color = discord.Color.green()
    else:
        remain = int(claim_at - time.time())
        h, m   = divmod(remain // 60, 60)
        status_text = f"⏳ 回収まで残り **{h}時間{m}分**"
        color = discord.Color.orange()

    embed = discord.Embed(title="📊 投資状況", color=color)
    embed.add_field(name="投資額", value=f"{inv['amount']:,}コイン", inline=True)
    embed.add_field(name="状態",   value=status_text, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# =========================
# /claiminvest（投資回収）
# =========================
@bot.tree.command(name="claiminvest", description="投資パックの結果を回収する")
async def claiminvest(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id  = str(interaction.user.id)
    data = load_data(guild_id)
    info = ensure_user_data(data, user_id)
    inv  = info.get("investment")

    if not inv:
        await interaction.response.send_message(
            "📊 投資中のパックがありません。`/invest` で投資を始めましょう！", ephemeral=True
        )
        return

    if time.time() < inv["invested_at"] + INVEST_DURATION:
        remain = int(inv["invested_at"] + INVEST_DURATION - time.time())
        h, m   = divmod(remain // 60, 60)
        await interaction.response.send_message(
            f"⏳ まだ回収できません。あと **{h}時間{m}分** 待ってください！", ephemeral=True
        )
        return

    amount               = inv["amount"]
    multiplier, label, emoji = draw_investment()
    payout               = int(amount * multiplier)
    profit               = payout - amount

    info["coins"]               = info.get("coins", 0) + payout
    info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + payout
    info["investment"]          = None
    save_data(guild_id, data)

    if multiplier >= 3.0:
        color = discord.Color.from_rgb(255, 215, 0)
    elif multiplier >= 1.5:
        color = discord.Color.green()
    elif multiplier == 1.0:
        color = discord.Color.blue()
    else:
        color = discord.Color.red()

    profit_text = f"+{profit:,}" if profit >= 0 else f"{profit:,}"
    embed = discord.Embed(
        title=f"{emoji} {label}",
        description=(
            f"投資額：**{amount:,}コイン**\n"
            f"倍率：**×{multiplier}**\n"
            f"回収額：**{payout:,}コイン**（{profit_text}コイン）"
        ),
        color=color
    )
    await interaction.response.send_message(embed=embed)


# =========================
# ROYAL PASS
# =========================
ROYAL_PASS_ROLE = "👑 ROYAL PASS"
ROYAL_PASS_XP_BONUS     = 1.2   # XP +20%
ROYAL_PASS_DAILY_BONUS  = 1.5   # デイリー報酬 +50%
ROYAL_PASS_DURATION     = 7 * 24 * 60 * 60

@bot.tree.command(name="buyroyal", description="👑 ROYAL PASSを購入する（50,000コイン・7日間）")
async def buyroyal(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id  = str(interaction.user.id)
    data     = load_data(guild_id)
    info     = ensure_user_data(data, user_id)
    price    = SHOP_ITEMS["royal_pass"]["price"]

    # 既に有効なROYAL PASSを持っているか確認
    cleanup_expired_buffs(info)
    if info.get("buffs", {}).get("royal_pass"):
        expires_at = info["buffs"]["royal_pass"].get("expires_at", 0)
        remain     = max(0, int(expires_at - time.time()))
        d, r       = divmod(remain, 86400)
        h, _       = divmod(r, 3600)
        await interaction.response.send_message(
            f"👑 ROYAL PASSは既に有効です！\n残り **{d}日{h}時間**",
            ephemeral=True
        )
        return

    if not spend_coins(data, user_id, price, "buy_royal_pass"):
        await interaction.response.send_message(
            f"コインが足りません。\n必要: **{price:,}コイン** ／ 所持: **{info.get('coins',0):,}コイン**",
            ephemeral=True
        )
        return

    # バフ付与（XP・デイリー両方）
    add_timed_buff(info, "royal_pass",        True,                  ROYAL_PASS_DURATION, "royal_pass")
    add_timed_buff(info, "xp_multiplier",     ROYAL_PASS_XP_BONUS,  ROYAL_PASS_DURATION, "royal_pass")
    add_timed_buff(info, "daily_multiplier",  ROYAL_PASS_DAILY_BONUS, ROYAL_PASS_DURATION, "royal_pass")

    # 特別ロール付与
    role = discord.utils.get(interaction.guild.roles, name=ROYAL_PASS_ROLE)
    if not role:
        try:
            role = await interaction.guild.create_role(
                name=ROYAL_PASS_ROLE,
                color=discord.Color.from_rgb(255, 215, 0),
                reason="ROYAL PASS購入"
            )
        except (discord.Forbidden, discord.HTTPException):
            role = None
    if role:
        try:
            await interaction.user.add_roles(role)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # コイン消費記録
    info["weekly_coins_spent"] = info.get("weekly_coins_spent", 0) + price
    shop_log = load_shop_log(guild_id)
    week_key = datetime.now(JST).strftime("%Y-W%W")
    shop_log.setdefault(week_key, {})
    shop_log[week_key]["royal_pass"] = shop_log[week_key].get("royal_pass", 0) + 1
    save_shop_log(guild_id, shop_log)
    save_data(guild_id, data)

    embed = discord.Embed(
        title="👑 ROYAL PASS 購入完了！",
        description=(
            f"{interaction.user.mention} がROYAL PASSを購入しました！\n\n"
            f"✨ XP獲得量 **+20%**（7日間）\n"
            f"🎁 デイリー報酬 **+50%**（7日間）\n"
            f"👑 特別ロール **{ROYAL_PASS_ROLE}** 付与！"
        ),
        color=discord.Color.from_rgb(255, 215, 0)
    )
    embed.set_footer(text="有効期間：7日間")
    await interaction.response.send_message(embed=embed)

    # 通知チャンネルにも告知
    ch_id = get_level_channel_id(guild_id)
    notify_ch = interaction.guild.get_channel(ch_id) if ch_id else None
    if notify_ch and notify_ch != interaction.channel:
        try:
            await notify_ch.send(
                f"👑 **{interaction.user.display_name}** が **ROYAL PASS** を購入しました！"
            )
        except (discord.Forbidden, discord.HTTPException):
            pass


# =========================
# /shopstats（人気アイテムTOP5・全期間）
# =========================
@bot.tree.command(name="shopstats", description="全期間の人気アイテムTOP5を表示")
async def shopstats(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    shop_log = load_shop_log(guild_id)

    # 全週のデータを合算
    all_time = {}
    for week_data in shop_log.values():
        for item_id, count in week_data.items():
            all_time[item_id] = all_time.get(item_id, 0) + count

    item_ranking = sorted(all_time.items(), key=lambda x: x[1], reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    item_lines = []
    for i, (item_id, count) in enumerate(item_ranking[:5], start=1):
        item_name = SHOP_ITEMS.get(item_id, {}).get("name", item_id)
        medal = medals[i - 1] if i <= 3 else f"`{i}.`"
        item_lines.append(f"{medal} **{item_name}** … {count}回購入")

    item_text = "\n".join(item_lines) if item_lines else "まだデータがありません。"

    embed = discord.Embed(
        title="🔥 人気アイテム TOP5（全期間）",
        description=item_text,
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed)


# =========================
# /servercoinsranking（全サーバー対抗・週間コイン獲得ランキング）
# =========================
@bot.tree.command(name="servercoinsranking", description="今週の全サーバーコイン獲得・消費ランキングを表示")
async def servercoinsranking(interaction: discord.Interaction):
    await interaction.response.defer()

    medals = ["🥇", "🥈", "🥉"]

    # --- 全サーバーのコイン集計 ---
    server_earned = []  # (guild_name, total_earned)
    server_spent  = []  # (guild_name, total_spent)

    for guild in bot.guilds:
        data = load_data(guild.id)
        total_earned = sum(
            info.get("weekly_coins_earned", 0)
            for uid, info in data.items()
            if uid != LAST_DECAY_KEY and isinstance(info, dict)
        )
        total_spent = sum(
            info.get("weekly_coins_spent", 0)
            for uid, info in data.items()
            if uid != LAST_DECAY_KEY and isinstance(info, dict)
        )
        if total_earned > 0:
            server_earned.append((guild.name, total_earned))
        if total_spent > 0:
            server_spent.append((guild.name, total_spent))

    server_earned.sort(key=lambda x: x[1], reverse=True)
    server_spent.sort(key=lambda x: x[1], reverse=True)

    def build_server_lines(ranking):
        lines = []
        for i, (name, coins) in enumerate(ranking[:10], start=1):
            medal = medals[i - 1] if i <= 3 else f"`{i}.`"
            lines.append(f"{medal} **{name}** … {coins:,}コイン")
        return "\n".join(lines) if lines else "まだデータがありません。"

    embed = discord.Embed(
        title="🌐 全サーバー 週間コインランキング",
        color=discord.Color.gold()
    )
    embed.add_field(name="📈 獲得数 TOP10", value=build_server_lines(server_earned), inline=False)
    embed.add_field(name="🛍️ 消費数 TOP10", value=build_server_lines(server_spent), inline=False)
    embed.set_footer(text="集計期間：今週（月曜リセット）")
    await interaction.followup.send(embed=embed)
@bot.tree.command(name="rates", description="現在のXPブースト倍率を確認する")
async def rates(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id = str(interaction.user.id)

    # サーバーブースト
    time_m = guild_time_boost.get(guild_id, 1)
    boss_m = guild_boss_boost.get(guild_id, 1)
    server_total = time_m * boss_m

    # ショップバフ（個人）
    data = load_data(guild_id)
    info = ensure_user_data(data, user_id)
    cleanup_expired_buffs(info)
    xp_buff = info.get("buffs", {}).get("xp_multiplier", {})
    shop_multi = xp_buff.get("value", 1.0) if xp_buff else 1.0
    shop_expires = xp_buff.get("expires_at", 0) if xp_buff else 0

    # 総合倍率
    total = server_total * shop_multi

    # 表示組み立て
    lines = []

    # 時間帯ブースト
    if time_m > 1:
        lines.append(f"⏰ 時間帯ブースト：**{time_m}倍** ✅")
    else:
        lines.append(f"⏰ 時間帯ブースト：なし")

    # ボス討伐ブースト
    if boss_m > 1:
        lines.append(f"⚔️ ボス討伐ブースト：**{boss_m}倍** ✅")
    else:
        lines.append(f"⚔️ ボス討伐ブースト：なし")

    # ショップバフ
    if shop_multi > 1:
        remaining = max(0, int(shop_expires - time.time()))
        h, m = divmod(remaining // 60, 60)
        time_str = f"{h}時間{m}分" if h > 0 else f"{m}分"
        lines.append(f"🛍️ ショップXPバフ：**{shop_multi}倍**（残り {time_str}）✅")
    else:
        lines.append(f"🛍️ ショップXPバフ：なし")

    # 総合
    lines.append("")
    if total > 1:
        lines.append(f"✨ **現在の総合倍率：{total:.2g}倍**")
    else:
        lines.append(f"✨ **現在の総合倍率：1倍（通常）**")

    embed = discord.Embed(
        title="📊 現在のXPブースト倍率",
        description="\n".join(lines),
        color=discord.Color.gold() if total > 1 else discord.Color.greyple()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================
# /levelstats（レベル分布）
# =========================
@bot.tree.command(name="levelstats", description="サーバーのレベル分布を表示")
async def levelstats(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    data = load_data(guild_id)

    bands = [
        ("Lv1〜9    MEMBER Lite", 1,    9),
        ("Lv10〜29  MEMBER",      10,   29),
        ("Lv30〜49  CORE",        30,   49),
        ("Lv50〜74  SELECT",      50,   74),
        ("Lv75〜99  PREMIUM",     75,   99),
        ("Lv100〜199 VIP Lite",   100,  199),
        ("Lv200〜499 VIP",        200,  499),
        ("Lv500〜    Legend",     500,  99999),
    ]

    counts = {label: 0 for label, _, _ in bands}
    total  = 0

    for uid, info in data.items():
        if uid == LAST_DECAY_KEY or not isinstance(info, dict):
            continue
        lv = info.get("level", 1)
        total += 1
        for label, lo, hi in bands:
            if lo <= lv <= hi:
                counts[label] += 1
                break

    lines = []
    for label, _, _ in bands:
        count = counts[label]
        pct   = (count / total * 100) if total > 0 else 0
        bar   = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
        lines.append(f"`{label}`\n　{bar} **{count}人** ({pct:.1f}%)")

    embed = discord.Embed(
        title="📊 レベル分布",
        description="\n\n".join(lines) if lines else "データなし",
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"総ユーザー数：{total}人")
    try:
        await interaction.response.send_message(embed=embed)
    except discord.HTTPException:
        await interaction.response.send_message("⚠️ 通信エラーが発生しました。もう一度お試しください。", ephemeral=True)


# =========================
# /peakhours（時間帯別アクティブ分析）
# =========================
@bot.tree.command(name="peakhours", description="メッセージが多い時間帯TOP5を表示")
async def peakhours(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    data = load_data(guild_id)

    hour_totals = [0] * 24
    for uid, info in data.items():
        if uid == LAST_DECAY_KEY or not isinstance(info, dict):
            continue
        for h in range(24):
            hour_totals[h] += info.get(f"hour_{h}", 0)

    ranked = sorted(enumerate(hour_totals), key=lambda x: x[1], reverse=True)

    medals = ["🥇", "🥈", "🥉", "`4.`", "`5.`"]
    lines  = []
    for i, (hour, count) in enumerate(ranked[:5]):
        if count == 0:
            break
        medal     = medals[i]
        bar_len   = int(count / max(hour_totals) * 20) if max(hour_totals) > 0 else 0
        bar       = "█" * bar_len + "░" * (20 - bar_len)
        lines.append(f"{medal} **{hour:02d}:00〜{hour:02d}:59**\n　{bar} {count:,}メッセージ")

    embed = discord.Embed(
        title="⏰ 時間帯別アクティブ TOP5（JST）",
        description="\n\n".join(lines) if lines else "まだデータがありません。",
        color=discord.Color.orange()
    )
    embed.set_footer(text="全期間の累計メッセージ数で集計")
    await interaction.response.send_message(embed=embed)


# =========================
# /bosstats（ボス討伐統計）
# =========================
@bot.tree.command(name="bosstats", description="ボスの討伐統計を表示")
async def bosstats(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    boss     = load_boss(guild_id)
    data     = load_data(guild_id)

    cleared    = boss.get("cleared", 0)
    active     = boss.get("active", False)
    current_hp = boss.get("hp", 0)
    max_hp     = boss.get("max_hp", 0)

    # 最多貢献者
    damage_dict      = boss.get("damage", {})
    top_contributors = sorted(damage_dict.items(), key=lambda x: x[1], reverse=True)[:3]

    medals = ["🥇", "🥈", "🥉"]
    contrib_lines = []
    for i, (uid, dmg) in enumerate(top_contributors):
        name = get_member_name(bot, uid)
        contrib_lines.append(f"{medals[i]} **{name}** … {dmg:,}ダメージ")

    # 通常ボス状況
    if active and max_hp > 0:
        hp_pct  = current_hp / max_hp * 100
        bar_len = int((1 - current_hp / max_hp) * 20)
        hp_bar  = "█" * bar_len + "░" * (20 - bar_len)
        boss_status = f"⚔️ **討伐中！**\nHP：`{hp_bar}` {current_hp:,}/{max_hp:,}（残り{hp_pct:.1f}%）"
    else:
        boss_status = "😴 現在ボスは出現していません"

    embed = discord.Embed(title="⚔️ ボス討伐統計", color=discord.Color.red())
    embed.add_field(name="✅ 累計討伐回数", value=f"**{cleared}回**", inline=True)
    embed.add_field(name="\u200b", value="\u200b", inline=False)
    embed.add_field(name="📍 通常ボス状況", value=boss_status, inline=False)
    embed.add_field(
        name="🏆 今週の貢献者TOP3",
        value="\n".join(contrib_lines) if contrib_lines else "まだデータなし",
        inline=False
    )
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # イベントボスセクション
    event_boss = load_event_boss(guild_id)
    if event_boss.get("active"):
        eb_max_hp = event_boss.get("max_hp", 1)
        eb_hp     = event_boss.get("hp", 0)
        eb_pct    = eb_hp / eb_max_hp * 100
        eb_bar    = "█" * int((1 - eb_hp / eb_max_hp) * 20) + "░" * int(eb_hp / eb_max_hp * 20)
        eb_name   = event_boss.get("name", "大魔王")

        eb_top = sorted(event_boss.get("damage", {}).items(), key=lambda x: x[1], reverse=True)[:3]
        eb_lines = []
        for i, (uid, dmg) in enumerate(eb_top):
            name = get_member_name(bot, uid)
            eb_lines.append(f"{medals[i]} **{name}** … {dmg:,}ダメージ")

        embed.add_field(
            name=f"🚨 イベントボス【{eb_name}】",
            value=(
                f"HP：`{eb_bar}` {eb_hp:,}/{eb_max_hp:,}（残り{eb_pct:.1f}%）\n"
                + ("\n".join(eb_lines) if eb_lines else "まだ誰も攻撃していません")
            ),
            inline=False
        )
    else:
        clears      = event_boss.get("consecutive_clears", 0)
        next_trigger = ((clears // EVENT_BOSS_CONSECUTIVE_CLEARS) + 1) * EVENT_BOSS_CONSECUTIVE_CLEARS
        remaining   = next_trigger - clears
        embed.add_field(
            name="🚨 イベントボス",
            value=(
                f"現在出現していません\n"
                f"累計討伐：**{clears}回** ／ 次の発動まであと **{remaining}回**"
            ),
            inline=False
        )

    embed.add_field(name="\u200b", value="\u200b", inline=False)

    # サーバーリンクボスセクション
    global_boss = load_global_event_boss()
    if global_boss.get("active"):
        gb_max  = global_boss.get("max_hp", 1)
        gb_hp   = global_boss.get("hp", 0)
        gb_pct  = gb_hp / gb_max * 100
        gb_bar  = "█" * int((1 - gb_hp / gb_max) * 20) + "░" * int(gb_hp / gb_max * 20)
        gb_name = global_boss.get("name", "大魔王")

        gb_top = sorted(global_boss.get("damage", {}).items(), key=lambda x: x[1], reverse=True)[:3]
        gb_lines = []
        for i, (uid, dmg) in enumerate(gb_top):
            name_str = get_member_name(bot, uid)
            gb_lines.append(f"{medals[i]} **{name_str}** … {dmg:,}ダメージ")

        embed.add_field(
            name=f"🌐 サーバーリンクボス【{gb_name}】",
            value=(
                f"HP：`{gb_bar}` {gb_hp:,}/{gb_max:,}（残り{gb_pct:.1f}%）\n"
                + ("\n".join(gb_lines) if gb_lines else "まだ誰も攻撃していません")
            ),
            inline=False
        )
    else:
        embed.add_field(
            name="🌐 サーバーリンクボス",
            value="現在出現していません",
            inline=False
        )

    await interaction.response.send_message(embed=embed)


# =========================
# /xpstats（全サーバー平均日次XP統計・bot管理者専用）
# =========================
@bot.tree.command(name="xpstats", description="【bot管理者専用】全サーバーの1人あたり平均日次XPを表示")
async def xpstats(interaction: discord.Interaction):
    if not is_bot_admin(interaction.user.id):
        await interaction.response.send_message("❌ このコマンドはbot管理者のみ使用できます。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # 週7日で割って1日平均を算出
    DAYS_IN_WEEK = 7
    all_server_avgs = []
    lines = []

    for guild in bot.guilds:
        data = load_data(guild.id)
        weekly_xp_list = [
            info.get("weekly_xp", 0)
            for uid, info in data.items()
            if uid != LAST_DECAY_KEY and isinstance(info, dict) and info.get("weekly_xp", 0) > 0
        ]
        if not weekly_xp_list:
            lines.append(f"**{guild.name}** … データなし")
            continue

        user_count   = len(weekly_xp_list)
        total_weekly = sum(weekly_xp_list)
        avg_daily    = total_weekly / DAYS_IN_WEEK / user_count

        all_server_avgs.append(avg_daily)
        lines.append(
            f"**{guild.name}**\n"
            f"　アクティブ人数：{user_count}人 ／ 1人あたり平均：**{avg_daily:,.1f} XP/日**"
        )

    # 全サーバー総合平均
    if all_server_avgs:
        global_avg = sum(all_server_avgs) / len(all_server_avgs)
        global_text = f"**{global_avg:,.1f} XP/日**"
    else:
        global_text = "データなし"

    embed = discord.Embed(
        title="📊 全サーバー 1人あたり平均日次XP",
        description="\n".join(lines) if lines else "データなし",
        color=discord.Color.blue()
    )
    embed.add_field(name="🌐 全サーバー総合平均", value=global_text, inline=False)
    embed.set_footer(text="週間XP ÷ 7日 ÷ アクティブ人数で算出（今週分）")
    await interaction.followup.send(embed=embed, ephemeral=True)
@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"{len(synced)} commands synced | Logged in as {bot.user}")
    print(f"接続中のサーバー: {[g.name for g in bot.guilds]}")

    if not weekly_ranking_task.is_running():
        weekly_ranking_task.start()
    if not weekly_mid_announcement.is_running():
        weekly_mid_announcement.start()
    if not decay_task.is_running():
        decay_task.start()
    if not xp_boost_scheduler.is_running():
        xp_boost_scheduler.start()
    if not boss_spawn_task.is_running():
        boss_spawn_task.start()
    if not boss_damage_report.is_running():
        boss_damage_report.start()
    if not server_ranking_task.is_running():
        server_ranking_task.start()
    if not rankdown_check_task.is_running():
        rankdown_check_task.start()
    if not daily_mission_announce_task.is_running():
        daily_mission_announce_task.start()
    if not server_link_boss_expiry_task.is_running():
        server_link_boss_expiry_task.start()

    # 既存サーバーのconfig確認・ランクロール更新
    for guild in bot.guilds:
        # config未登録のサーバーはチャンネルを探して登録
        if not get_level_channel_id(guild.id):
            existing = discord.utils.get(guild.text_channels, name="レベル通知")
            if existing:
                set_level_channel_id(guild.id, existing.id)
                print(f"[{guild.name}] レベル通知チャンネルを自動登録しました (ID: {existing.id})")

        data = load_data(guild.id)
        # on_readyでの一括ロール更新は403エラーの原因になるためスキップ
        # ロール更新はメッセージ送信・レベルアップ時に個別に行う

# =========================
# Run
# =========================
if __name__ == "__main__":
    keep_alive()
    token = os.environ.get("TOKEN")
    if token:
        bot.run(token)
    else:
        print("Error: TOKEN not set")
