import discord
from discord.ext import commands
import json
import os
import time
import random
import asyncio
from flask import Flask
from threading import Thread
from datetime import datetime

# =========================
# Flask（Bot常時起動用）
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
# Bot設定
# =========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "levels.json"
cooldowns = {}
vc_users = {}

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
# レベル設定
# =========================
rank_roles = {
    1: "MEMBER Lite",
    10: "MEMBER",
    30: "CORE",
    50: "SELECT",
    75: "PREMIUM",
    100: "VIP Lite",
    200: "VIP"
}

permanent_roles = {
    3: "PHOTO+"
}

weekly_roles = {
    1: "🥇週間王者",
    2: "🥈週間準王",
    3: "🥉週間三位"
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
                if channel:
                    await channel.send(f"📸 {role_name} を獲得しました！")

        # ランクロール
        target_role_name = rank_roles.get(new_level)
        if target_role_name:
            target_role = discord.utils.get(guild.roles, name=target_role_name)
            if target_role:
                for role in member.roles:
                    if role.name in rank_roles.values():
                        await member.remove_roles(role)

                await member.add_roles(target_role)
                if channel:
                    await channel.send(
                        f"🏆 {target_role_name} ランクに昇格しました！"
                    )

# =========================
# メッセージXP処理
# =========================
@bot.event
async def on_message(message):

    if message.author.bot:
        return

    user_id = str(message.author.id)
    current_time = time.time()

    # 10秒クールタイム
    if user_id in cooldowns:
        if current_time - cooldowns[user_id] < 10:
            return

    cooldowns[user_id] = current_time
    data = load_data()

    if user_id not in data:
        data[user_id] = {
            "xp": 0,
            "level": 1,
            "last_daily": "",
            "weekly_xp": 0
        }

    today = datetime.utcnow().strftime("%Y-%m-%d")
    daily_bonus = 0

    # デイリーボーナス
    if data[user_id]["last_daily"] != today:
        daily_bonus = 100
        data[user_id]["xp"] += daily_bonus
        data[user_id]["weekly_xp"] += daily_bonus
        data[user_id]["last_daily"] = today

        await message.channel.send(
            f"🎁 {message.author.mention} デイリーボーナス！ +{daily_bonus}XP"
        )

    # 通常XP
    xp_gain = random.randint(5, 20)
    data[user_id]["xp"] += xp_gain
    data[user_id]["weekly_xp"] += xp_gain

    await check_level_up(
        message.author,
        message.channel,
        data,
        user_id
    )

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

    # VC参加
    if after.channel and not before.channel:

        vc_users[user_id] = True

        while vc_users.get(user_id):

            await asyncio.sleep(300)

            if not member.voice or not member.voice.channel:
                break

            if len(member.voice.channel.members) < 2:
                continue

            data = load_data()

            if user_id not in data:
                data[user_id] = {
                    "xp": 0,
                    "level": 1,
                    "last_daily": "",
                    "weekly_xp": 0
                }

            vc_xp = 10
            data[user_id]["xp"] += vc_xp
            data[user_id]["weekly_xp"] += vc_xp

            await check_level_up(
                member,
                member.guild.system_channel,  # テキスト送信用
                data,
                user_id
            )

            save_data(data)

    # VC退出
    if before.channel and not after.channel:
        vc_users[user_id] = False

# =========================
# /rank
# =========================
@bot.tree.command(name="rank", description="自分のレベルを確認")
async def rank(interaction: discord.Interaction):

    await interaction.response.defer()

    user_id = str(interaction.user.id)
    data = load_data()

    if user_id not in data:
        await interaction.followup.send("まだXPがありません！")
        return

    xp = data[user_id]["xp"]
    level = data[user_id]["level"]
    required_xp = level * 100

    progress = xp / required_xp
    filled = int(20 * progress)

    bar = "█" * filled + "░" * (20 - filled)
    percent = int(progress * 100)

    embed = discord.Embed(
        title="📊 あなたのランク情報",
        color=discord.Color.blue()
    )

    embed.add_field(name="レベル", value=f"Lv {level}", inline=True)
    embed.add_field(
        name="XPバー",
        value=f"{bar} {percent}%\n{xp} / {required_xp}",
        inline=False
    )

    embed.set_footer(text="Level System")
    await interaction.followup.send(embed=embed)


# =========================
# /top コマンド
# =========================
@bot.tree.command(name="top", description="サーバーランキングを見る")
async def top(interaction: discord.Interaction):

    await interaction.response.defer()

    data = load_data()

    if not data:
        await interaction.followup.send("まだデータがありません！")
        return

    sorted_users = sorted(
        data.items(),
        key=lambda x: (x[1]["level"], x[1]["xp"]),
        reverse=True
    )

    embed = discord.Embed(
        title="🏆 全サーバーランキング TOP10",
        color=discord.Color.gold()
    )

    description = ""
    for i, (user_id, info) in enumerate(sorted_users[:10], start=1):
        try:
            # infoの中身が空だったり、level/xpが無い場合を防ぐ
            level = info.get("level", 0)
            xp = info.get("xp", 0)
            description += f"**{i}位** <@{user_id}> - Lv{level} ({xp}XP)\n"
        except Exception as e:
            print(f"ランキング表示エラー: {user_id} / {e}")
            continue

    embed.description = description

    await interaction.followup.send(embed=embed)

# =========================
# 起動時
# =========================
@bot.event
async def on_ready():

    print("=== DATA CHECK ===")
    print(load_data())
    print("==================")

    synced = await bot.tree.sync()
    print(f"{len(synced)}個のコマンドを同期しました")
    print(f"Logged in as {bot.user}")

# =========================
# 実行
# =========================
if __name__ == "__main__":
    keep_alive()
    token = os.environ.get("TOKEN")

    if token:
        bot.run(token)
    else:
        print("Error: TOKEN not found.")