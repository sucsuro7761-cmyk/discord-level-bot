import discord
from discord.ext import commands, tasks
from discord import app_commands
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

JST = pytz.timezone('Asia/Tokyo')

# =========================
# Flask
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
# データ
# =========================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return {}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

# =========================
# 週間計算
# =========================
def get_week_start_timestamp():
    now = datetime.now(JST)
    days_since_monday = now.weekday()
    week_start = now - timedelta(days=days_since_monday)
    week_start = week_start.replace(hour=18, minute=0, second=0, microsecond=0)
    return int(week_start.timestamp())

def get_weekly_xp_sum(data, user_id):
    week_start = get_week_start_timestamp()
    total = 0
    if user_id in data and "xp_history" in data[user_id]:
        for timestamp, xp in data[user_id]["xp_history"]:
            if timestamp >= week_start:
                total += xp
    return total

def get_weekly_top(data):
    ranking = []
    for user_id in data:
        weekly_xp = get_weekly_xp_sum(data, user_id)
        ranking.append((user_id, weekly_xp))

    ranking.sort(key=lambda x: x[1], reverse=True)
    return ranking

def get_total_top_3():
    data = load_data()
    ranking = []

    for user_id, user_data in data.items():
        total_xp = user_data.get("xp", 0)
        ranking.append((user_id, total_xp))

    ranking.sort(key=lambda x: x[1], reverse=True)
    return ranking[:3]

# =========================
# レベルアップ
# =========================
async def check_level_up(member, channel, data, user_id):
    while True:
        current_xp = data[user_id]["xp"]
        current_level = data[user_id]["level"]
        required_xp = current_level * 100

        if current_xp < required_xp:
            break

        data[user_id]["xp"] -= required_xp
        data[user_id]["level"] += 1

        if channel:
            await channel.send(
                f"🎉 {member.mention} が Lv{data[user_id]['level']} になりました！"
            )

# =========================
# 🔥 スラッシュコマンド
# =========================

@bot.tree.command(name="myxp", description="現在のXPを確認")
async def myxp(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    data = load_data()

    if user_id not in data:
        await interaction.response.send_message("まだデータがありません！")
        return

    total_xp = data[user_id]["xp"]
    weekly_xp = get_weekly_xp_sum(data, user_id)

    await interaction.response.send_message(
        f"📊 {interaction.user.mention}\n"
        f"総合XP: {total_xp}\n"
        f"今週XP: {weekly_xp}"
    )

@bot.tree.command(name="rank", description="現在のレベルを確認")
async def rank(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    data = load_data()

    if user_id not in data:
        await interaction.response.send_message("まだデータがありません！")
        return

    level = data[user_id]["level"]
    xp = data[user_id]["xp"]
    required = level * 100

    await interaction.response.send_message(
        f"🏆 {interaction.user.mention}\n"
        f"レベル: {level}\n"
        f"XP: {xp}/{required}"
    )

@bot.tree.command(name="top", description="総合XPランキングTOP3")
async def top(interaction: discord.Interaction):
    top_3 = get_total_top_3()

    if not top_3:
        await interaction.response.send_message("まだランキングデータがありません！")
        return

    message = "🏆 総合XPランキング TOP3\n\n"

    for i, (user_id, xp) in enumerate(top_3, 1):
        member = interaction.guild.get_member(int(user_id))
        if member:
            medal = ["🥇", "🥈", "🥉"][i - 1]
            message += f"{medal} {member.mention} - {xp}XP\n"

    await interaction.response.send_message(message)

# =========================
# 🏆 週間王者タスク
# =========================
@tasks.loop(minutes=1)
async def weekly_champion_task():
    now = datetime.now(JST)

    if now.weekday() == 0 and now.hour == 18 and now.minute == 0:
        data = load_data()
        ranking = get_weekly_top(data)

        if not ranking:
            return

        winner_id, winner_xp = ranking[0]
        guild = bot.get_guild(GUILD_ID)
        channel = guild.get_channel(LEVEL_CHANNEL_ID)

        member = guild.get_member(int(winner_id))
        if member and channel:
            await channel.send(
                f"🏆 今週の王者は {member.mention}！\n"
                f"獲得XP: {winner_xp}"
            )

        # 履歴リセット
        for user_id in data:
            data[user_id]["xp_history"] = []

        save_data(data)

# =========================
# メッセージXP
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
        data[user_id] = {
            "xp": 0,
            "level": 1,
            "xp_history": []
        }

    xp_gain = random.randint(5, 20)
    data[user_id]["xp"] += xp_gain
    data[user_id]["xp_history"].append([int(time.time()), xp_gain])

    await check_level_up(message.author, message.channel, data, user_id)
    save_data(data)
    await bot.process_commands(message)

# =========================
# 起動
# =========================
@bot.event
async def on_ready():
    synced = await bot.tree.sync()
    print(f"✅ Synced {len(synced)} commands")
    print(f"Logged in as {bot.user}")

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