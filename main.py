import discord
from discord.ext import commands, tasks
import json
import os
import time
import random
import asyncio
from flask import Flask
from threading import Thread
from datetime import datetime, timezone, timedelta
import pytz

# =========================
# 基本設定
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "/data/levels.json"
LEVEL_CHANNEL_ID = 1477839103151177864
GUILD_ID = 1332006524465188904

cooldowns = {}
vc_users = {}

# JST タイムゾーン
JST = pytz.timezone('Asia/Tokyo')

# =========================
# Flask（常時起動用）
# =========================
app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run():
    app.run(host='0.0.0.0', port=5000)

def keep_alive():
    Thread(target=run).start()

# =========================
# データ読み書き
# =========================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# =========================
# ロール設定
# =========================
rank_roles = {
    1: "MEMBER Lite",
    5: "MEMBER",
    15: "CORE",
    35: "SELECT",
    70: "PREMIUM",
    100: "VIP Lite",
    200: "VIP"
}

permanent_roles = {
    3: "PHOTO+"
}

# 週間王者ロール
weekly_champion_roles = {
    1: "週間王者🥇",
    2: "週間準王🥈",
    3: "週間三位🥉"
}

# =========================
# 週間XP管理用ユーティリティ
# =========================
def get_week_start_timestamp():
    """現在の週の開始時刻（月曜18:00 JST）を取得"""
    now = datetime.now(JST)
    
    # 月曜日の18:00までの経過時間を計算
    days_since_monday = now.weekday()  # 0=月, 6=日
    current_time_seconds = now.hour * 3600 + now.minute * 60 + now.second
    monday_18_seconds = 18 * 3600
    
    if days_since_monday == 0 and current_time_seconds < monday_18_seconds:
        # 月曜日で18:00前なら前週の月曜18:00から
        week_start = now - timedelta(days=7)
    else:
        # それ以外は今週の月曜18:00から
        week_start = now - timedelta(days=days_since_monday)
    
    week_start = week_start.replace(hour=18, minute=0, second=0, microsecond=0)
    return int(week_start.timestamp())

def get_weekly_xp_sum(data, user_id):
    """指定ユーザーの今週のXP合計を取得"""
    week_start = get_week_start_timestamp()
    total_xp = 0
    
    if user_id in data and "xp_history" in data[user_id]:
        for timestamp, xp_gain in data[user_id]["xp_history"]:
            if timestamp >= week_start:
                total_xp += xp_gain
    
    return total_xp

def get_top_3_users():
    """週間XP TOP3を取得"""
    data = load_data()
    user_xp_list = []
    
    for user_id, user_data in data.items():
        weekly_xp = get_weekly_xp_sum(data, user_id)
        if weekly_xp > 0:
            user_xp_list.append((user_id, weekly_xp))
    
    # XPでソート（降順）
    user_xp_list.sort(key=lambda x: x[1], reverse=True)
    
    return user_xp_list[:3]

# =========================
# レベルアップ処理
# =========================
async def check_level_up(member, channel, data, user_id):

    guild = member.guild

    while True:
        current_xp = data[user_id]["xp"]
        current_level = data[user_id]["level"]
        required_xp = current_level * 100

        if current_xp < required_xp:
            break

        data[user_id]["xp"] -= required_xp
        data[user_id]["level"] += 1
        new_level = data[user_id]["level"]

        if channel:
            await channel.send(
                f"🎉 {member.mention} が Lv{new_level} になりました！"
            )

        # 永久ロール
        if new_level in permanent_roles:
            role_name = permanent_roles[new_level]
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                await member.add_roles(role)

        # ランクロール
        target_role_name = rank_roles.get(new_level)
        if target_role_name:
            target_role = discord.utils.get(guild.roles, name=target_role_name)
            if target_role:
                for role in member.roles:
                    if role.name in rank_roles.values():
                        await member.remove_roles(role)
                await member.add_roles(target_role)

# =========================
# 週間ロール管理
# =========================
async def assign_weekly_champion_roles(guild):
    """TOP3ユーザーに週間王者ロールを付与"""
    top_3 = get_top_3_users()
    
    # 既存の週間ロールを全員から剥奪
    for rank, role_name in weekly_champion_roles.items():
        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            for member in guild.members:
                if role in member.roles:
                    await member.remove_roles(role)
                    print(f"Removed {role_name} from {member.name}")
    
    # TOP3に新しいロールを付与
    for rank, (user_id, weekly_xp) in enumerate(top_3, 1):
        member = guild.get_member(int(user_id))
        if member:
            role_name = weekly_champion_roles[rank]
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                await member.add_roles(role)
                print(f"Added {role_name} to {member.name} (XP: {weekly_xp})")
    
    # 通知
    level_channel = bot.get_channel(LEVEL_CHANNEL_ID)
    if level_channel:
        embed = discord.Embed(
            title="📊 週間ランキング更新！",
            description="先週の週間ランキングが確定しました",
            color=discord.Color.gold()
        )
        
        for rank, (user_id, weekly_xp) in enumerate(top_3, 1):
            member = guild.get_member(int(user_id))
            if member:
                medal = ["🥇", "🥈", "🥉"][rank - 1]
                embed.add_field(
                    name=f"{medal} 第{rank}位",
                    value=f"{member.mention}\nXP: {weekly_xp}",
                    inline=False
                )
        
        await level_channel.send(embed=embed)

# =========================
# 週間ランキング定時実行
# =========================
@tasks.loop(hours=24)
async def weekly_champion_task():
    """毎週月曜日18:00 JSTに実行"""
    now = datetime.now(JST)
    
    # 月曜日の18:00か確認
    if now.weekday() == 0 and 18 <= now.hour < 19:  # 月曜日の18時台
        guild = bot.get_guild(GUILD_ID)
        if guild:
            await assign_weekly_champion_roles(guild)
            print(f"Weekly champion roles assigned at {now}")

@weekly_champion_task.before_loop
async def before_weekly_task():
    await bot.wait_until_ready()

# =========================
# メッセージXP処理
# =========================
@bot.event
async def on_message(message):

    if message.author.bot:
        return

    user_id = str(message.author.id)
    current_time = time.time()

    if user_id in cooldowns:
        if current_time - cooldowns[user_id] < 10:
            return

    cooldowns[user_id] = current_time
    data = load_data()

    if user_id not in data:
        data[user_id] = {}

    data[user_id].setdefault("xp", 0)
    data[user_id].setdefault("level", 1)
    data[user_id].setdefault("last_daily", "")
    data[user_id].setdefault("weekly_xp", 0)
    data[user_id].setdefault("xp_history", [])

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if data[user_id]["last_daily"] != today:
        bonus = 100
        data[user_id]["xp"] += bonus
        data[user_id]["weekly_xp"] += bonus
        data[user_id]["xp_history"].append([int(time.time()), bonus])
        data[user_id]["last_daily"] = today
        await message.channel.send(
            f"🎁 {message.author.mention} デイリーボーナス！ +{bonus}XP"
        )

    xp_gain = random.randint(5, 20)
    data[user_id]["xp"] += xp_gain
    data[user_id]["weekly_xp"] += xp_gain
    data[user_id]["xp_history"].append([int(time.time()), xp_gain])

    await check_level_up(message.author, message.channel, data, user_id)

    save_data(data)
    await bot.process_commands(message)

# =========================
# VC XP処理
# =========================
@bot.event
async def on_voice_state_update(member, before, after):

    if member.bot:
        return

    user_id = str(member.id)

    if after.channel and not before.channel:

        vc_users[user_id] = True

        while vc_users.get(user_id):

            await asyncio.sleep(30)

            if not member.voice or not member.voice.channel:
                break

            if len(member.voice.channel.members) < 2:
                continue

            data = load_data()

            if user_id not in data:
                data[user_id] = {}

            data[user_id].setdefault("xp", 0)
            data[user_id].setdefault("level", 1)
            data[user_id].setdefault("last_daily", "")
            data[user_id].setdefault("weekly_xp", 0)
            data[user_id].setdefault("xp_history", [])

            vc_xp = 10
            data[user_id]["xp"] += vc_xp
            data[user_id]["weekly_xp"] += vc_xp
            data[user_id]["xp_history"].append([int(time.time()), vc_xp])

            # 🔥 専用チャンネルへ送信
            level_channel = bot.get_channel(LEVEL_CHANNEL_ID)

            await check_level_up(
                member,
                level_channel,
                data,
                user_id
            )

            save_data(data)

    if before.channel and not after.channel:
        vc_users[user_id] = False

# =========================
# 起動時
# =========================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user}")
    
    # タスク開始
    if not weekly_champion_task.is_running():
        weekly_champion_task.start()

# =========================
# 実行
# =========================
if __name__ == "__main__":
    keep_alive()
    token = os.environ.get("TOKEN")
    if token:
        bot.run(token)