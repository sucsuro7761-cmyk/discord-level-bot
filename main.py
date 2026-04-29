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
from flask import Flask
from threading import Thread
from datetime import datetime, timezone, timedelta
import pytz

# =========================
# Config
# =========================
DECAY_PERCENT = 0.05
LAST_DECAY_KEY = "last_decay"
DATA_DIR = "/data"
JST = pytz.timezone("Asia/Tokyo")
# =========================
# Coin / Shop Config
# =========================
COIN_DAILY_CAP = 1500

SHOP_ITEMS = {
    "xp_small": {
        "name": "XP\u30d6\u30fc\u30b9\u30c8\uff08\u5c0f\uff09",
        "price": 500,
        "description": "XP\u7372\u5f97\u91cf\u304c1.5\u500d\u306b\u306a\u308a\u307e\u3059\uff0830\u5206\uff09",
        "buff_type": "xp_multiplier",
        "value": 1.5,
        "duration": 30 * 60,
    },
    "xp_medium": {
        "name": "XP\u30d6\u30fc\u30b9\u30c8\uff08\u4e2d\uff09",
        "price": 1200,
        "description": "XP\u7372\u5f97\u91cf\u304c2\u500d\u306b\u306a\u308a\u307e\u3059\uff0830\u5206\uff09",
        "buff_type": "xp_multiplier",
        "value": 2.0,
        "duration": 30 * 60,
    },
    "daily_boost": {
        "name": "\u30c7\u30a4\u30ea\u30fc\u5f37\u5316",
        "price": 700,
        "description": "\u30c7\u30a4\u30ea\u30fc\u5831\u916c\u304c1.5\u500d\u306b\u306a\u308a\u307e\u3059\uff0824\u6642\u9593\uff09",
        "buff_type": "daily_multiplier",
        "value": 1.5,
        "duration": 24 * 60 * 60,
    },
    "attack_up": {
        "name": "\u653b\u6483\u529b\u30a2\u30c3\u30d7",
        "price": 600,
        "description": "\u30dc\u30b9\u3078\u306e\u30c0\u30e1\u30fc\u30b8\u304c1.2\u500d\u306b\u306a\u308a\u307e\u3059\uff0815\u5206\uff09",
        "buff_type": "damage_multiplier",
        "value": 1.2,
        "duration": 15 * 60,
    },
    "crit_up": {
        "name": "クリティカル強化",
        "price": 800,
        "description": "クリティカル発生率がアップ！獲得XPに超高倍率ダメージ（15分）",
        "buff_type": "crit_bonus",
        "value": True,
        "duration": 15 * 60,
    },
    "boss_slayer": {
        "name": "\u30dc\u30b9\u7279\u52b9",
        "price": 1000,
        "description": "\u30dc\u30b9\u3078\u306e\u30c0\u30e1\u30fc\u30b8\u304c1.3\u500d\u306b\u306a\u308a\u307e\u3059\uff0815\u5206\uff09",
        "buff_type": "boss_damage_multiplier",
        "value": 1.3,
        "duration": 15 * 60,
    },
}

# =========================
# クリティカルシステム
# =========================
# バフあり/なしで確率が変わる
# ミニCT: バフあり5% / バフなし2.5%  → ×5
# CT:     バフあり3%  / バフなし1.5%  → ×20
# 超CT:   バフあり0.5%  / バフなし0.25% → ×50
# 超+CT:  バフあり0.1%/ バフなし0.05% → ×100

CRIT_TABLE = [
    # (名前, バフあり確率, バフなし確率, 倍率, 絵文字)
    ("超+CT", 0.001, 0.0005, 50, "💥"),
    ("超CT",  0.005,  0.0025,  25, "⚡"),
    ("CT",    0.03,  0.015,   10, "🔥"),
    ("ミニCT",0.05,  0.025,    5, "✨"),
]

def calc_crit(base_xp, has_crit_buff):
    """クリティカル判定を行い (最終XP, クリット名orNone, 倍率) を返す"""
    r = random.random()
    cumulative = 0.0
    for name, prob_buff, prob_normal, multiplier, emoji in CRIT_TABLE:
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

cooldowns = {}
vc_users = {}

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
BOSS_CLEAR_ROLE = "⚔️ボス討伐者"

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
    (200, 999, "VIP"),
    (1000, 9999, "Legend")
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

    target_role = None
    for min_lv, max_lv, role_name in rank_roles:
        if min_lv <= level <= max_lv:
            target_role = discord.utils.get(guild.roles, name=role_name)
            break

    current_rank_role = None
    for role in member.roles:
        for _, _, r_name in rank_roles:
            if role.name == r_name:
                current_rank_role = role
                break

    if current_rank_role == target_role:
        return
    if current_rank_role:
        await member.remove_roles(current_rank_role)
    if target_role:
        await member.add_roles(target_role)

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

        await update_rank_role(member, new_level)

        # レベルアップコイン付与（レベルに応じて増加、最大500）
        coin_reward = min(100 + (new_level * 10), 500)
        info = ensure_user_data(data, user_id)
        info["coins"] = info.get("coins", 0) + coin_reward

        if notify_channel:
            await notify_channel.send(f"🎉 {member.mention} が Lv{new_level} になりました！ 💰 +{coin_reward}コイン")

        if new_level in permanent_roles:
            role_name = permanent_roles[new_level]
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                await member.add_roles(role)
                if notify_channel:
                    await notify_channel.send(f"📸 {role_name} を獲得しました！")

# =========================
# Message XP
# =========================
@bot.event
async def on_message(message):
    if message.author.bot:
        return

    guild_id = message.guild.id
    user_id = str(message.author.id)
    current_time = time.time()

    ck = f"{guild_id}:{user_id}"
    if ck in cooldowns and current_time - cooldowns[ck] < 10:
        return
    cooldowns[ck] = current_time

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
        today_earned = info.get("coin_daily_earned", 0)
        if today_earned < COIN_DAILY_CAP:
            add_amount = min(streak_coins, COIN_DAILY_CAP - today_earned)
            info["coins"] = info.get("coins", 0) + add_amount
            info["coin_daily_earned"] = today_earned + add_amount
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

    # クリティカル発生時に通知
    if crit_name:
        await message.channel.send(
            f"{crit_name} {message.author.display_name} **+{xp_gain:,}XP**（{crit_multi}倍！）"
        )

    await check_level_up(message.author, data, user_id)
    save_data(guild_id, data)

    boss = load_boss(guild_id)
    if boss.get("active"):
        # ボスダメージバフ適用
        boss_dmg_buff = info_tmp.get("buffs", {}).get("boss_damage_multiplier", {})
        boss_multi = boss_dmg_buff.get("value", 1.0) if boss_dmg_buff else 1.0
        actual_dmg = int(xp_gain * boss_multi)
        boss["damage"][user_id] = boss["damage"].get(user_id, 0) + actual_dmg
        boss["hp"] = max(0, boss["hp"] - actual_dmg)
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

    await bot.process_commands(message)

# =========================
# VC XP
# =========================
@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    guild_id = member.guild.id
    user_id = str(member.id)
    ck = f"{guild_id}:{user_id}"

    if after.channel and not before.channel:
        vc_users[ck] = True
        while vc_users.get(ck):
            await asyncio.sleep(30)
            if not member.voice or not member.voice.channel:
                break
            if len(member.voice.channel.members) < 2:
                continue

            data = load_data(guild_id)
            if user_id not in data:
                data[user_id] = {}
            data[user_id].setdefault("xp", 0)
            data[user_id].setdefault("level", 1)
            data[user_id].setdefault("last_daily", "")
            data[user_id].setdefault("weekly_xp", 0)

            boost = get_boost(guild_id)
            # ミュート中は2XP、ミュート解除（発言中）は15XP
            is_muted = member.voice.self_mute or member.voice.mute
            base_xp_vc = 2 if is_muted else 15

            # クリティカル判定
            vc_info = data.get(user_id, {})
            cleanup_expired_buffs(vc_info)
            has_crit_buff_vc = bool(vc_info.get("buffs", {}).get("crit_bonus"))
            base_xp_boosted = int(base_xp_vc * boost["multiplier"])
            gain, crit_name_vc, crit_multi_vc = calc_crit(base_xp_boosted, has_crit_buff_vc)

            data[user_id]["xp"] += gain
            data[user_id]["weekly_xp"] += gain
            data[user_id]["weekly_vc_xp"] = data[user_id].get("weekly_vc_xp", 0) + gain

            # クリティカル発生時に通知チャンネルへ
            if crit_name_vc:
                ch_id = get_level_channel_id(guild_id)
                crit_ch = member.guild.get_channel(ch_id) if ch_id else None
                if crit_ch:
                    await crit_ch.send(
                        f"{crit_name_vc} {member.display_name} **+{gain:,}XP**（VC {crit_multi_vc}倍！）"
                    )

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

    if before.channel and not after.channel:
        vc_users[ck] = False


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

    if not spend_coins(data, user_id, item["price"], f"buy_{item_id}"):
        await interaction.response.send_message(
            f"\u30b3\u30a4\u30f3\u304c\u8db3\u308a\u307e\u305b\u3093\u3002\n"
            f"\u5fc5\u8981: **{item['price']:,}\u30b3\u30a4\u30f3**\n"
            f"\u6240\u6301: **{info.get('coins', 0):,}\u30b3\u30a4\u30f3**",
            ephemeral=True
        )
        return

    add_timed_buff(info, item["buff_type"], item["value"], item["duration"], item_id)
    save_data(interaction.guild.id, data)

    await interaction.response.send_message(
        f"\u2705 **{item['name']}** \u3092\u8cfc\u5165\u3057\u307e\u3057\u305f\uff01\n"
        f"\u52b9\u679c: {item['description']}",
        ephemeral=True
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
                    await member.remove_roles(role)

        weekly_coin_rewards = {1: 3000, 2: 2000, 3: 1000}
        text = ""
        for i, (user_id, info) in enumerate(top3, start=1):
            role = discord.utils.get(guild.roles, name=weekly_roles[i])
            member = guild.get_member(int(user_id))
            if role and member:
                await member.add_roles(role)
            coin_r = weekly_coin_rewards.get(i, 0)
            info["coins"] = info.get("coins", 0) + coin_r
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
                data[uid]["coin_daily_earned"] = 0  # 日次上限もリセット
        save_data(gid, data)

# =========================
# XP Decay Task
# =========================
@tasks.loop(hours=24)
async def decay_task():
    await bot.wait_until_ready()
    today = datetime.now(JST).strftime("%Y-%m-%d")

    for guild in bot.guilds:
        gid = guild.id
        data = load_data(gid)
        if not data:
            continue

        if data.get(LAST_DECAY_KEY) == today:
            continue

        for uid, info in data.items():
            if uid == LAST_DECAY_KEY or not isinstance(info, dict):
                continue
            current_xp = info.get("xp", 0)
            if current_xp > 0:
                info["xp"] = max(0, int(current_xp * (1 - DECAY_PERCENT)))

        data[LAST_DECAY_KEY] = today
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

# =========================
# イベントボス：クリア処理
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
            await member.add_roles(role)

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

    role = discord.utils.get(guild.roles, name=BOSS_CLEAR_ROLE)
    for uid, dmg in boss["damage"].items():
        if dmg <= 0:
            continue
        member = guild.get_member(int(uid))
        if member and role:
            await member.add_roles(role)

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
        embed.add_field(name="報酬", value=f"🔥 **次のボス出現まで XP 2倍ブースト** 発動！\n`{BOSS_CLEAR_ROLE}` ロール付与！")
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
# 週ボス：ダメージ報告（0時・6時・12時・18時）
# =========================
_boss_report_fired = {}  # { "YYYY-MM-DD_HH": True }

@tasks.loop(minutes=1)
async def boss_damage_report():
    await bot.wait_until_ready()

    now = datetime.now(JST)
    if now.hour not in [0, 6, 12, 18] or now.minute != 0:
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

    for guild in bot.guilds:
        gid = guild.id
        boss = load_boss(gid)
        if not boss.get("active"):
            continue

        ch_id = get_level_channel_id(gid)
        notify_channel = guild.get_channel(ch_id) if ch_id else None
        if not notify_channel:
            continue

        max_hp = boss.get("max_hp", 1)
        current_hp = boss.get("hp", 0)
        progress = (max_hp - current_hp) / max_hp
        filled = int(20 * progress)
        bar = "█" * filled + "░" * (20 - filled)
        percent = int(progress * 100)

        sorted_dmg = sorted(boss["damage"].items(), key=lambda x: x[1], reverse=True)
        top_text = ""
        medals = ["🥇", "🥈", "🥉"]
        for i, (uid, dmg) in enumerate(sorted_dmg[:3]):
            top_text += f"{medals[i]} <@{uid}> - {dmg}ダメージ\n"

        if not top_text:
            top_text = "まだ誰も攻撃していません！"

        embed = discord.Embed(title="⚔️ 週ボス ダメージレポート", color=discord.Color.orange())
        embed.add_field(
            name="❤️ ボスHP",
            value=f"{bar} {percent}%\n{current_hp:,} / {max_hp:,}",
            inline=False
        )
        embed.add_field(name="🏆 ダメージTOP3", value=top_text, inline=False)
        embed.set_footer(text="メッセージを送って攻撃しよう！")
        await notify_channel.send(embed=embed)


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
    save_data(guild_id, data)

    embed = discord.Embed(
        title="📦 宝箱を開けた！",
        description=f"{interaction.user.mention} が宝箱を開けました！\n💰 **+{coin_gain:,}コイン** 獲得！",
        color=discord.Color.gold()
    )
    embed.set_footer(text="次の宝箱は1時間後に開けられます")
    await interaction.response.send_message(embed=embed)

# =========================
# /dailymission（デイリーミッション確認・受取）
# =========================
@bot.tree.command(name="dailymission", description="デイリーミッション（今日100XP獲得 → 200コイン）")
async def dailymission(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user_id = str(interaction.user.id)
    data = load_data(guild_id)
    info = ensure_user_data(data, user_id)

    today = datetime.now(JST).strftime("%Y-%m-%d")
    mission_claimed = info.get("daily_mission_claimed", "")
    weekly_chat_xp = info.get("weekly_chat_xp", 0) + info.get("weekly_vc_xp", 0)

    # 今週獲得XP（チャット+VC）で判定
    today_xp = info.get("today_xp", 0)

    if mission_claimed == today:
        await interaction.response.send_message(
            "✅ 今日のデイリーミッションは既に受け取り済みです！明日また来てね。",
            ephemeral=True
        )
        return

    # 今日のXP獲得量を計算（last_dailyが今日ならOK）
    last_daily = info.get("last_daily", "")
    if last_daily != today:
        embed = discord.Embed(
            title="🎯 デイリーミッション",
            description=(
                "**今日のミッション**\n"
                "📝 今日メッセージを送って100XP以上獲得する\n"
                "💰 達成報酬: **+200コイン**\n\n"
                "⏳ まだ未達成です。メッセージを送ってXPを貯めよう！"
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)
        return

    # 達成済み（今日デイリーボーナスを受け取っている = ログイン済み）
    today_earned = info.get("coin_daily_earned", 0)
    if today_earned >= COIN_DAILY_CAP:
        await interaction.response.send_message(
            f"💸 今日のコイン獲得上限（{COIN_DAILY_CAP:,}コイン）に達しています。",
            ephemeral=True
        )
        return

    mission_coins = min(200, COIN_DAILY_CAP - today_earned)
    info["coins"] = info.get("coins", 0) + mission_coins
    info["coin_daily_earned"] = today_earned + mission_coins
    info["daily_mission_claimed"] = today
    save_data(guild_id, data)

    embed = discord.Embed(
        title="🎯 デイリーミッション達成！",
        description=f"今日のログインミッション達成！\n💰 **+{mission_coins}コイン** 獲得！",
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
# /boss コマンド
# =========================
@bot.tree.command(name="boss", description="今週のボス状況を確認")
async def boss_status(interaction: discord.Interaction):
    boss = load_boss(interaction.guild.id)

    if not boss.get("active"):
        await interaction.response.send_message("現在ボスは出現していません。月曜6時に出現します！")
        return

    max_hp = boss.get("max_hp", 1)
    current_hp = boss.get("hp", 0)
    progress = (max_hp - current_hp) / max_hp
    filled = int(20 * progress)
    bar = "█" * filled + "░" * (20 - filled)
    percent = int(progress * 100)

    user_id = str(interaction.user.id)
    my_dmg = boss["damage"].get(user_id, 0)

    sorted_dmg = sorted(boss["damage"].items(), key=lambda x: x[1], reverse=True)
    top_text = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, dmg) in enumerate(sorted_dmg[:3]):
        top_text += f"{medals[i]} <@{uid}> - {dmg}ダメージ\n"
    if not top_text:
        top_text = "まだ誰も攻撃していません！"

    embed = discord.Embed(
        title=f"👹 週ボス状況 - Week {boss.get('week', 1)}",
        color=discord.Color.red()
    )
    embed.add_field(name="❤️ ボスHP", value=f"{bar} {percent}%\n{current_hp:,} / {max_hp:,}", inline=False)
    embed.add_field(name="🏆 ダメージTOP3", value=top_text, inline=False)
    embed.add_field(name="⚔️ あなたのダメージ", value=f"{my_dmg}ダメージ", inline=False)
    await interaction.response.send_message(embed=embed)

# =========================
# /eventboss コマンド群
# =========================
eventboss_group = discord.app_commands.Group(name="eventboss", description="イベントボス管理")

@eventboss_group.command(name="start", description="イベントボスを手動で出現させる（管理者用）")
@discord.app_commands.checks.has_permissions(administrator=True)
async def eventboss_start(
    interaction: discord.Interaction,
    name: str = "大魔王",
    hp: int = 150000,
    days: int = 7,
    boost: int = 3
):
    gid = interaction.guild.id
    event_boss = load_event_boss(gid)
    if event_boss.get("active"):
        await interaction.response.send_message("⚠️ すでにイベントボスが出現中です！", ephemeral=True)
        return
    await interaction.response.send_message(
        f"✅ イベントボス【{name}】を召喚します！\nHP: {hp:,} / 期間: {days}日 / ブースト: {boost}倍",
        ephemeral=True
    )
    await spawn_event_boss(interaction.guild, name, hp=hp, days=days, boost_multiplier=boost)

@eventboss_group.command(name="status", description="イベントボスの状況を確認")
async def eventboss_status(interaction: discord.Interaction):
    event_boss = load_event_boss(interaction.guild.id)
    if not event_boss.get("active"):
        clears = event_boss.get("consecutive_clears", 0)
        await interaction.response.send_message(
            f"現在イベントボスは出現していません。\n"
            f"通常ボス累計クリア数: **{clears}回** / 発動条件: **{EVENT_BOSS_CONSECUTIVE_CLEARS}回**",
            ephemeral=True
        )
        return

    max_hp = event_boss.get("max_hp", 1)
    current_hp = event_boss.get("hp", 0)
    progress = (max_hp - current_hp) / max_hp
    filled = int(20 * progress)
    bar = "█" * filled + "░" * (20 - filled)
    percent = int(progress * 100)

    user_id = str(interaction.user.id)
    my_dmg = event_boss["damage"].get(user_id, 0)

    sorted_dmg = sorted(event_boss["damage"].items(), key=lambda x: x[1], reverse=True)
    top_text = ""
    medals = ["🥇", "🥈", "🥉"]
    for i, (uid, dmg) in enumerate(sorted_dmg[:3]):
        top_text += f"{medals[i]} <@{uid}> - {dmg:,}ダメージ\n"
    if not top_text:
        top_text = "まだ誰も攻撃していません！"

    boss_name = event_boss.get("name", "大魔王")
    embed = discord.Embed(
        title=f"🚨 イベントボス【{boss_name}】状況",
        color=discord.Color.from_rgb(255, 100, 0)
    )
    embed.add_field(name="❤️ ボスHP", value=f"{bar} {percent}%\n{current_hp:,} / {max_hp:,}", inline=False)
    embed.add_field(name="🏆 ダメージTOP3", value=top_text, inline=False)
    embed.add_field(name="⚔️ あなたのダメージ", value=f"{my_dmg:,}ダメージ", inline=False)
    await interaction.response.send_message(embed=embed)

@eventboss_group.command(name="setname", description="次のイベントボスの名前を設定（管理者用）")
@discord.app_commands.checks.has_permissions(administrator=True)
async def eventboss_setname(interaction: discord.Interaction, name: str):
    gid = interaction.guild.id
    event_boss = load_event_boss(gid)
    event_boss["name"] = name
    save_event_boss(gid, event_boss)
    await interaction.response.send_message(f"✅ 次のイベントボス名を **{name}** に設定しました！", ephemeral=True)

bot.tree.add_command(eventboss_group)

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
                "**使い方**\n"
                "メッセージを送るとXPが貯まります！\n"
                "VCに2人以上いるとXPが貯まります！\n"
                "毎週月曜に週ボスが出現します！"
            ),
            color=discord.Color.green()
        )
        embed.add_field(
            name="📋 コマンド一覧",
            value=(
                "`/rank` - 自分のランク確認\n"
                "`/myxp` - XP詳細確認\n"
                "`/top` - ランキングTOP10\n"
                "`/boss` - 週ボス状況確認\n"
                "`/setchannel` - 通知チャンネル変更（管理者）\n"
                "`/setuproles` - ロール・チャンネル再セットアップ（管理者）\n"
                "`/userdata` - ユーザーデータ確認（管理者）\n"
                "`/alldata` - 全データCSV出力（管理者）"
            )
        )
        await notify_channel.send(embed=embed)

# =========================
# 起動時
# =========================
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

    # 既存サーバーのconfig確認・ランクロール更新
    for guild in bot.guilds:
        # config未登録のサーバーはチャンネルを探して登録
        if not get_level_channel_id(guild.id):
            existing = discord.utils.get(guild.text_channels, name="レベル通知")
            if existing:
                set_level_channel_id(guild.id, existing.id)
                print(f"[{guild.name}] レベル通知チャンネルを自動登録しました (ID: {existing.id})")

        data = load_data(guild.id)
        for user_id, info in data.items():
            if user_id == LAST_DECAY_KEY:
                continue
            member = guild.get_member(int(user_id))
            if member:
                await update_rank_role(member, info.get("level", 1))
                await asyncio.sleep(0.5)

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
