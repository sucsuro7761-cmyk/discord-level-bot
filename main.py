import discord
from discord.ext import commands
import json
import os
import time
import random
import asyncio
from flask import Flask
from threading import Thread
from datetime import datetime, timezone

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

cooldowns = {}
vc_users = {}

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

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if data[user_id]["last_daily"] != today:
        bonus = 100
        data[user_id]["xp"] += bonus
        data[user_id]["weekly_xp"] += bonus
        data[user_id]["last_daily"] = today
        await message.channel.send(
            f"🎁 {message.author.mention} デイリーボーナス！ +{bonus}XP"
        )

    xp_gain = random.randint(5, 20)
    data[user_id]["xp"] += xp_gain
    data[user_id]["weekly_xp"] += xp_gain

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

            vc_xp = 10
            data[user_id]["xp"] += vc_xp
            data[user_id]["weekly_xp"] += vc_xp

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

# =========================
# 実行
# =========================
if __name__ == "__main__":
    keep_alive()
    token = os.environ.get("TOKEN")
    if token:
        bot.run(token)