import discord
from discord.ext import commands
import json
import os
import time
import random
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

# =========================
# レベルアップ処理
# =========================
async def check_level_up(message, data, user_id):

    guild = message.guild

    while True:
        current_xp = data[user_id]["xp"]
        current_level = data[user_id]["level"]
        required_xp = current_level * 100

        if current_xp < required_xp:
            break

        # レベルアップ
        data[user_id]["xp"] -= required_xp
        data[user_id]["level"] += 1
        new_level = data[user_id]["level"]

        await message.channel.send(
            f"🎉 {message.author.mention} が Lv{new_level} になりました！"
        )

        # 永久ロール付与
        if new_level in permanent_roles:
            role_name = permanent_roles[new_level]
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                await message.author.add_roles(role)
                await message.channel.send(f"📸 {role_name} を獲得しました！")

        # ランクロール管理
        target_role_name = rank_roles.get(new_level)
        if target_role_name:
            target_role = discord.utils.get(guild.roles, name=target_role_name)
            if target_role:
                # 既存ランクロール削除
                for role in message.author.roles:
                    if role.name in rank_roles.values():
                        await message.author.remove_roles(role)

                await message.author.add_roles(target_role)
                await message.channel.send(
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
            "last_daily": ""
        }

    # =========================
    # デイリーボーナス（自動）
    # =========================
    today = datetime.utcnow().strftime("%Y-%m-%d")

    if data[user_id].get("last_daily") != today:
        daily_bonus = 100
        data[user_id]["xp"] += daily_bonus
        data[user_id]["last_daily"] = today

        await message.channel.send(
            f"🎁 {message.author.mention} デイリーボーナス！ +{daily_bonus}XP"
        )

    # =========================
    # 通常XP
    # =========================
    xp_gain = random.randint(5, 20)
    data[user_id]["xp"] += xp_gain

    # レベルチェック
    await check_level_up(message, data, user_id)

    save_data(data)
    await bot.process_commands(message)

@bot.event
async def on_voice_state_update(member, before, after):

    # Botは無視
    if member.bot:
        return

    user_id = str(member.id)

    # VCに参加した時
    if after.channel and not before.channel:

        vc_users[user_id] = True

        while vc_users.get(user_id):

            await asyncio.sleep(300)  # 5分（300秒）

            # まだVCにいるか確認
            if not member.voice or not member.voice.channel:
                break

            # 1人VC防止（同じチャンネルに2人以上）
            if len(member.voice.channel.members) < 2:
                continue

            data = load_data()

            if user_id not in data:
                data[user_id] = {
                    "xp": 0,
                    "level": 1,
                    "last_daily": ""
                }

            vc_xp = 10
            data[user_id]["xp"] += vc_xp

            save_data(data)

            await check_level_up(
                await member.guild.fetch_channel(member.voice.channel.id),
                data,
                user_id
            )

    # VC退出時
    if before.channel and not after.channel:
        vc_users[user_id] = False

# =========================
# /rank コマンド
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

    bar_length = 20
    progress = xp / required_xp
    filled_length = int(bar_length * progress)

    bar = "█" * filled_length + "░" * (bar_length - filled_length)
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
        user = await bot.fetch_user(int(user_id))
        description += f"**{i}位** {user.name} - Lv{info['level']} ({info['xp']}XP)\n"

    embed.description = description

    await interaction.followup.send(embed=embed)

# =========================
# 起動時
# =========================
@bot.event
async def on_ready():
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
        print("Error: TOKEN not found in environment variables.")