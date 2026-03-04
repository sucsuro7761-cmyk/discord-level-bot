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
# Config
# ========================
DECAY_PERCENT = 0.05
LAST_DECAY_KEY = "last_decay"

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "/data/levels.json"
LEVEL_CHANNEL_ID = 1477839103151177864

cooldowns = {}
vc_users = {}

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
# Data read/write
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
# Rank definitions
# =========================
rank_roles = [
    (1, 9, "MEMBER Lite"),
    (10, 29, "MEMBER"),
    (30, 49, "CORE"),
    (50, 74, "SELECT"),
    (75, 99, "PREMIUM"),
    (100, 199, "VIP Lite"),
    (200, 9999, "VIP")
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

    # Determine target role
    target_role = None
    for min_lv, max_lv, role_name in rank_roles:
        if min_lv <= level <= max_lv:
            target_role = discord.utils.get(guild.roles, name=role_name)
            break

    # Find current rank role
    current_rank_role = None
    for role in member.roles:
        for _, _, r_name in rank_roles:
            if role.name == r_name:
                current_rank_role = role
                break

    # If no change needed
    if current_rank_role == target_role:
        return

    # Remove old
    if current_rank_role:
        await member.remove_roles(current_rank_role)

    # Add new
    if target_role:
        await member.add_roles(target_role)

# =========================
# Level-up check
# =========================
async def check_level_up(member, channel, data, user_id):
    guild = member.guild
    notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

    while True:
        current_xp = data[user_id]["xp"]
        current_level = data[user_id]["level"]
        required_xp = current_level * 100

        if current_xp < required_xp:
            break

        data[user_id]["xp"] -= required_xp
        data[user_id]["level"] += 1
        new_level = data[user_id]["level"]

        # rank update
        await update_rank_role(member, new_level)

        # Level-up notify
        if notify_channel:
            await notify_channel.send(f"🎉 {member.mention} が Lv{new_level} になりました！")

        # Permanent role
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

    user_id = str(message.author.id)
    current_time = time.time()

    if user_id in cooldowns and current_time - cooldowns[user_id] < 10:
        return
    cooldowns[user_id] = current_time

    data = load_data()
    if user_id not in data:
        data[user_id] = {}

    data[user_id].setdefault("xp", 0)
    data[user_id].setdefault("level", 1)
    data[user_id].setdefault("last_daily", "")
    data[user_id].setdefault("weekly_xp", 0)
    data.setdefault(LAST_DECAY_KEY, "")

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if data[user_id]["last_daily"] != today:
        data[user_id]["xp"] += 100
        data[user_id]["weekly_xp"] += 100
        data[user_id]["last_daily"] = today
        await message.channel.send(f"🎁 {message.author.mention} デイリーボーナス！ +100XP")

    xp_gain = random.randint(5, 20)
    data[user_id]["xp"] += xp_gain
    data[user_id]["weekly_xp"] += xp_gain

    await check_level_up(message.author, message.channel, data, user_id)
    save_data(data)
    await bot.process_commands(message)

# =========================
# VC XP
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

            data[user_id]["xp"] += 10
            data[user_id]["weekly_xp"] += 10

            await check_level_up(member, member.guild.system_channel, data, user_id)
            save_data(data)

    if before.channel and not after.channel:
        vc_users[user_id] = False

# =========================
# /rank
# =========================
@bot.tree.command(name="rank", description="自分のレベルを確認")
async def rank(interaction: discord.Interaction):
    await interaction.response.defer()
    data = load_data()
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
@bot.tree.command(name="top", description="サーバーランキングを見る")
async def top(interaction: discord.Interaction):
    await interaction.response.defer()
    data = load_data()
    if not data:
        await interaction.followup.send("まだデータがありません！")
        return

    sorted_users = sorted(data.items(), key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)), reverse=True)
    embed = discord.Embed(title="🏆 全サーバーランキング TOP10", color=discord.Color.gold())
    desc = ""
    for i, (user_id, info) in enumerate(sorted_users[:10], start=1):
        desc += f"**{i}位** <@{user_id}> - Lv{info.get('level',0)} ({info.get('xp',0)}XP)\n"
    embed.description = desc
    await interaction.followup.send(embed=embed)

# =========================
# /myxp
# =========================
@bot.tree.command(name="myxp", description="自分のXPやレベルを確認")
async def myxp(interaction: discord.Interaction):
    data = load_data()
    user_id = str(interaction.user.id)
    if user_id not in data:
        await interaction.response.send_message("まだデータがありません！")
        return

    info = data[user_id]
    embed = discord.Embed(title=f"📊 {interaction.user.name} のデータ", color=discord.Color.green())
    embed.add_field(name="レベル", value=f"Lv {info.get('level',1)}")
    embed.add_field(name="XP", value=f"{info.get('xp',0)} XP")
    embed.add_field(name="今週のXP", value=f"{info.get('weekly_xp',0)} XP")
    embed.add_field(name="最終デイリーボーナス", value=info.get('last_daily','なし'))
    await interaction.response.send_message(embed=embed)

# =========================
# 週間ランキング（Final）
# =========================
JST = pytz.timezone("Asia/Tokyo")

@tasks.loop(minutes=1)
async def weekly_ranking_task():
    now = datetime.now(JST)
    if now.weekday()==0 and now.hour==18 and now.minute<2:
        data = load_data()
        if not data:
            return
        guild = bot.guilds[0]
        notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

        sorted_users = sorted(data.items(), key=lambda x: x[1].get("weekly_xp",0), reverse=True)
        top3 = sorted_users[:3]

        for role_name in weekly_roles.values():
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                for member in role.members:
                    await member.remove_roles(role)

        text = ""
        for i,(user_id,info) in enumerate(top3,start=1):
            role = discord.utils.get(guild.roles, name=weekly_roles[i])
            member = guild.get_member(int(user_id))
            if role and member:
                await member.add_roles(role)
            text += f"{['🥇','🥈','🥉'][i-1]} <@{user_id}> - {info.get('weekly_xp',0)} XP\n"

        if notify_channel:
            embed=discord.Embed(title="🏆 週間ランキング結果発表！",description=text,color=discord.Color.gold())
            await notify_channel.send(embed=embed)

        for uid in data:
            data[uid]["weekly_xp"]=0
        save_data(data)

# =========================
# 週間ランキング中間発表（毎日21時）
# =========================
@tasks.loop(minutes=1)
async def weekly_mid_announcement():
    now = datetime.now(JST)
    if now.hour==21 and now.minute<2:
        data = load_data()
        if not data:
            return
        guild = bot.guilds[0]
        notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)
        sorted_users = sorted(data.items(), key=lambda x: x[1].get("weekly_xp",0), reverse=True)
        top5=sorted_users[:5]
        desc=""
        medals=["🥇","🥈","🥉","④","⑤"]
        for i,(uid,info) in enumerate(top5):
            desc+=f"{medals[i]} <@{uid}> - {info.get('weekly_xp',0)} XP\n"
        if notify_channel:
            embed=discord.Embed(title="📊 週間ランキング中間発表",description=desc,color=discord.Color.blue())
            embed.set_footer(text="最終結果は月曜18:00に発表！")
            await notify_channel.send(embed=embed)

# =========================
# 3ヶ月レベル減衰
# =========================
@tasks.loop(hours=24)
async def decay_task():
    data=load_data()
    if not data:
        return

    now = datetime.now(timezone.utc)
    last_str=data.get(LAST_DECAY_KEY,"")

    # 初回-safe
    if not last_str:
        data[LAST_DECAY_KEY]=now.strftime("%Y-%m-%d")
        save_data(data)
        return

    last_dt = datetime.strptime(last_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if (now-last_dt).days<90:
        return

    guild=bot.guilds[0]
    notify_channel=guild.get_channel(LEVEL_CHANNEL_ID)

    results=""
    for user_id,info in data.items():
        if user_id==LAST_DECAY_KEY:
            continue

        level=info.get("level",1)
        decay=int(level*DECAY_PERCENT)
        new_level=max(1,level-decay)
        info["level"]=new_level
        info["xp"]=0

        member=guild.get_member(int(user_id))
        if member:
            await update_rank_role(member,new_level)

        results+=f"<@{user_id}> Lv{level} → Lv{new_level}\n"

    data[LAST_DECAY_KEY]=now.strftime("%Y-%m-%d")
    save_data(data)

    if notify_channel and results:
        embed=discord.Embed(title="⚔ レベル減衰が発生しました",description=results,color=discord.Color.red())
        await notify_channel.send(embed=embed)

# =========================
# 起動時
# =========================
@bot.event
async def on_ready():
    synced=await bot.tree.sync()
    print(f"{len(synced)} commands synced | Logged in as {bot.user}")

    if not weekly_ranking_task.is_running():
        weekly_ranking_task.start()
    if not weekly_mid_announcement.is_running():
        weekly_mid_announcement.start()
    if not decay_task.is_running():
        decay_task.start()
    
    data = load_data()
    for guild in bot.guilds:
        for user_id, info in data.items():
            if user_id == LAST_DECAY_KEY:
                continue

            member = guild.get_member(int(user_id))
            if member:
                await update_rank_role(member, info.get("level", 1))
    # 🔥 ここまで追加

    if not weekly_ranking_task.is_running():
        weekly_ranking_task.start()
    if not weekly_mid_announcement.is_running():
        weekly_mid_announcement.start()
    if not decay_task.is_running():
        decay_task.start()

# =========================
# Run
# =========================
if __name__=="__main__":
    keep_alive()
    token=os.environ.get("TOKEN")
    if token:
        bot.run(token)
    else:
        print("Error: TOKEN not set")