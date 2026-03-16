import discord
from discord.ext import commands, tasks
import json
import os
import time
import random
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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

cooldowns = {}
vc_users = {}

# =========================
# XP BOOST SYSTEM（サーバーごと）
# =========================
# { guild_id: {"multiplier": 1, "active": False} }
guild_boost = {}

def get_boost(guild_id):
    return guild_boost.get(guild_id, {"multiplier": 1, "active": False})

def set_boost(guild_id, multiplier, active):
    guild_boost[guild_id] = {"multiplier": multiplier, "active": active}

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
# ファイルパス（サーバーごと）
# =========================
def data_file(guild_id):
    return f"{DATA_DIR}/levels_{guild_id}.json"

def boss_file(guild_id):
    return f"{DATA_DIR}/boss_{guild_id}.json"

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

        if notify_channel:
            await notify_channel.send(f"🎉 {member.mention} が Lv{new_level} になりました！")

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
    data.setdefault(LAST_DECAY_KEY, "")

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

        if streak == 1:
            streak_msg = "🎁 **デイリーボーナス！**"
        elif streak < 5:
            streak_msg = f"🔥 **{streak}日連続ログイン！**"
        else:
            streak_msg = f"🌟 **{streak}日連続ログイン！MAX ボーナス！**"

        await message.channel.send(
            f"{streak_msg}\n"
            f"{message.author.mention} **+{bonus}XP** "
            f"（連続{streak}日目）"
        )

    boost = get_boost(guild_id)
    xp_gain = int(random.randint(5, 20) * boost["multiplier"])
    data[user_id]["xp"] += xp_gain
    data[user_id]["weekly_xp"] += xp_gain

    await check_level_up(message.author, data, user_id)
    save_data(guild_id, data)

    boss = load_boss(guild_id)
    if boss.get("active"):
        boss["damage"][user_id] = boss["damage"].get(user_id, 0) + xp_gain
        boss["hp"] = max(0, boss["hp"] - xp_gain)
        if boss["hp"] <= 0:
            boss["active"] = False
            boss["cleared"] += 1
            save_boss(guild_id, boss)
            await handle_boss_clear(message.guild, boss)
        else:
            save_boss(guild_id, boss)

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
            gain = int(10 * boost["multiplier"])
            data[user_id]["xp"] += gain
            data[user_id]["weekly_xp"] += gain

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

    if before.channel and not after.channel:
        vc_users[ck] = False

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

        text = ""
        for i, (user_id, info) in enumerate(top3, start=1):
            role = discord.utils.get(guild.roles, name=weekly_roles[i])
            member = guild.get_member(int(user_id))
            if role and member:
                await member.add_roles(role)
            text += f"{['🥇','🥈','🥉'][i-1]} <@{user_id}> - {info.get('weekly_xp', 0)} XP\n"

        if notify_channel:
            embed = discord.Embed(
                title="🏆 週間ランキング結果発表！",
                description=text,
                color=discord.Color.gold()
            )
            await notify_channel.send(embed=embed)

        for uid in data:
            if uid != LAST_DECAY_KEY:
                data[uid]["weekly_xp"] = 0
        save_data(gid, data)

# =========================
# XP BOOST TASK（全サーバー）
# =========================
@tasks.loop(hours=24)
async def xp_boost_scheduler():
    await bot.wait_until_ready()

    morning_hour = random.randint(8, 11)
    night_hour = random.randint(18, 22)

    for hour in [morning_hour, night_hour]:
        now = datetime.now(JST)
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)

        if now > target:
            continue

        wait = (target - now).total_seconds()
        await asyncio.sleep(wait)

        multiplier = 3 if random.random() < 0.05 else 2

        for guild in bot.guilds:
            set_boost(guild.id, multiplier, True)
            ch_id = get_level_channel_id(guild.id)
            channel = guild.get_channel(ch_id) if ch_id else None
            if channel:
                await channel.send(
                    f"🔥 **XP BOOST START!**\n"
                    f"XPが **{multiplier}倍** になりました！\n"
                    f"1時間限定！"
                )

        await asyncio.sleep(3600)

        for guild in bot.guilds:
            set_boost(guild.id, 1, False)
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
# 3ヶ月レベル減衰（全サーバー）
# =========================
@tasks.loop(hours=24)
async def decay_task():
    for guild in bot.guilds:
        gid = guild.id
        data = load_data(gid)
        if not data:
            continue

        now = datetime.now(timezone.utc)
        last_str = data.get(LAST_DECAY_KEY, "")

        if not last_str:
            data[LAST_DECAY_KEY] = now.strftime("%Y-%m-%d")
            save_data(gid, data)
            continue

        last_dt = datetime.strptime(last_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if (now - last_dt).days < 90:
            continue

        ch_id = get_level_channel_id(gid)
        notify_channel = guild.get_channel(ch_id) if ch_id else None

        results = ""
        for user_id, info in data.items():
            if user_id == LAST_DECAY_KEY:
                continue
            level = info.get("level", 1)
            decay = int(level * DECAY_PERCENT)
            new_level = max(1, level - decay)
            info["level"] = new_level
            info["xp"] = 0
            member = guild.get_member(int(user_id))
            if member:
                await update_rank_role(member, new_level)
            results += f"<@{user_id}> Lv{level} → Lv{new_level}\n"

        data[LAST_DECAY_KEY] = now.strftime("%Y-%m-%d")
        save_data(gid, data)

        if notify_channel and results:
            embed = discord.Embed(
                title="⚔ レベル減衰が発生しました",
                description=results,
                color=discord.Color.red()
            )
            await notify_channel.send(embed=embed)

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

# =========================
# 週ボス：討伐ブースト
# =========================
async def boss_clear_boost(guild, notify_channel):
    gid = guild.id
    set_boost(gid, 2, True)
    if notify_channel:
        await notify_channel.send("🔥 **討伐記念 XP 2倍ブースト開始！** 次のボス出現まで継続！")

    now = datetime.now(JST)
    days_until_monday = (7 - now.weekday()) % 7 or 7
    next_monday = (now + timedelta(days=days_until_monday)).replace(
        hour=6, minute=0, second=0, microsecond=0
    )
    wait_seconds = (next_monday - now).total_seconds()
    await asyncio.sleep(wait_seconds)

    set_boost(gid, 1, False)
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

        set_boost(gid, 1, False)

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
            embed.set_footer(text="2時間ごとにダメージ報告あり")
            await notify_channel.send(embed=embed)

# =========================
# 週ボス：2時間ごとダメージ報告（全サーバー）
# =========================
@tasks.loop(hours=2)
async def boss_damage_report():
    await bot.wait_until_ready()

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
        {"name": "Legend",       "color": discord.Color.from_rgb(255, 215,   0)},
        {"name": "🥇週間王者",   "color": discord.Color.from_rgb(255, 168,   0)},
        {"name": "🥈週間準王",   "color": discord.Color.from_rgb(153, 153, 153)},
        {"name": "🥉週間三位",   "color": discord.Color.from_rgb(180, 100,  40)},
        {"name": "PHOTO+",       "color": discord.Color.from_rgb(255, 255, 255)},
        {"name": "⚔️ボス討伐者", "color": discord.Color.from_rgb(220,  50,  50)},
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
