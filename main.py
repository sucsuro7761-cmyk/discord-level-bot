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
from discord.ext import tasks
import pytz
from datetime import timedelta

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

vc_users = {}

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
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "/data/levels.json"
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

    # 安全初期化
    if user_id not in data:
        data[user_id] = {}

    data[user_id].setdefault("xp", 0)
    data[user_id].setdefault("level", 1)
    data[user_id].setdefault("last_daily", "")
    data[user_id].setdefault("weekly_xp", 0)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
# VC XP処理（安全版）
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

            await asyncio.sleep(30)

            if not member.voice or not member.voice.channel:
                break

            if len(member.voice.channel.members) < 2:
                continue

            data = load_data()

            if user_id not in data:
                data[user_id] = {}

            # 安全初期化
            data[user_id].setdefault("xp", 0)
            data[user_id].setdefault("level", 1)
            data[user_id].setdefault("last_daily", "")
            data[user_id].setdefault("weekly_xp", 0)

            vc_xp = 10
            data[user_id]["xp"] += vc_xp
            data[user_id]["weekly_xp"] += vc_xp

            # system_channel が存在しない場合は None
            text_channel = member.guild.system_channel if member.guild.system_channel else None

            await check_level_up(
                member,
                text_channel,
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

    xp = data[user_id].get("xp", 0)
    level = data[user_id].get("level", 1)
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
        key=lambda x: (x[1].get("level",0), x[1].get("xp",0)),
        reverse=True
    )

    embed = discord.Embed(
        title="🏆 全サーバーランキング TOP10",
        color=discord.Color.gold()
    )

    description = ""
    for i, (user_id, info) in enumerate(sorted_users[:10], start=1):
        level = info.get("level", 0)
        xp = info.get("xp", 0)
        description += f"**{i}位** <@{user_id}> - Lv{level} ({xp}XP)\n"

    embed.description = description

    await interaction.followup.send(embed=embed)

# =========================
# /myxp コマンド
# 自分のXPやレベルを確認する
# =========================
@bot.tree.command(name="myxp", description="自分のXPやレベルを確認")
async def myxp(interaction: discord.Interaction):
    data = load_data()
    user_id = str(interaction.user.id)

    if user_id not in data:
        await interaction.response.send_message("まだデータがありません！")
        return

    # 安全に取得
    info = data[user_id]
    xp = info.get("xp", 0)
    level = info.get("level", 1)
    weekly_xp = info.get("weekly_xp", 0)
    last_daily = info.get("last_daily", "なし")

    # メッセージ送信
    embed = discord.Embed(
        title=f"📊 {interaction.user.name} のデータ",
        color=discord.Color.green()
    )
    embed.add_field(name="レベル", value=f"Lv {level}", inline=True)
    embed.add_field(name="XP", value=f"{xp} XP", inline=True)
    embed.add_field(name="今週のXP", value=f"{weekly_xp} XP", inline=True)
    embed.add_field(name="最終デイリーボーナス", value=last_daily, inline=False)

    await interaction.response.send_message(embed=embed)

JST = pytz.timezone("Asia/Tokyo")

@tasks.loop(minutes=1)
async def weekly_ranking_task():

    now = datetime.now(JST)

    # 月曜18:00のみ実行
    if now.weekday() == 0 and now.hour == 18 and now.minute == 0:

        data = load_data()

        if not data:
            return

        guild = bot.guilds[0]

        # ランキング作成（weekly_xp順）
        sorted_users = sorted(
            data.items(),
            key=lambda x: x[1].get("weekly_xp", 0),
            reverse=True
        )

        top3 = sorted_users[:3]

        # 既存の週間ロールを全員から剥がす
        for role_name in weekly_roles.values():
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                for member in role.members:
                    await member.remove_roles(role)

        # Top3に付与
        for rank, (user_id, info) in enumerate(top3, start=1):

            role_name = weekly_roles.get(rank)
            role = discord.utils.get(guild.roles, name=role_name)

            if role:
                member = guild.get_member(int(user_id))
                if member:
                    await member.add_roles(role)

        # weekly_xpリセット
        for user_id in data:
            data[user_id]["weekly_xp"] = 0

        save_data(data)

# =========================
# 起動時
# =========================
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

    if not weekly_ranking_task.is_running():
        weekly_ranking_task.start()

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
