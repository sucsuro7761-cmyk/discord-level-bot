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
DECAY_PERCENT = 0.05
DECAY_MONTHS = 3
LAST_DECAY_KEY = "last_decay"

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
LEVEL_CHANNEL_ID = 1477839103151177864
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
rank_roles = [
    (1, 9, "MEMBER Lite"),
    (10, 29, "MEMBER"),
    (30, 49, "CORE"),
    (50, 74, "SELECT"),
    (75, 99, "PREMIUM"),
    (100, 199, "VIP Lite"),
    (200, 9999, "VIP")
]

# =========================
# ランクロール更新関数（高速版）
# =========================
async def update_rank_role(member, level):

    guild = member.guild

    # 付与すべきランクロールを決定
    target_role = None
    for min_lv, max_lv, role_name in rank_roles:
        if min_lv <= level <= max_lv:
            target_role = discord.utils.get(guild.roles, name=role_name)
            break

    # 現在持っているランクロール
    current_rank_role = None
    for role in member.roles:
        for _, _, r_name in rank_roles:
            if role.name == r_name:
                current_rank_role = role
                break

    # 同じロールなら何もしない（超重要）
    if current_rank_role == target_role:
        return

    # 古いロールを外す
    if current_rank_role:
        await member.remove_roles(current_rank_role)

    # 新しいロールを付与
    if target_role:
        await member.add_roles(target_role)

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
        
        await update_rank_role(member, new_level)

        # レベルアップ通知
        if notify_channel:
            await notify_channel.send(
                f"🎉 {member.mention} が Lv{new_level} になりました！"
            )

        # =========================
        # 永久ロール
        # =========================
        if new_level in permanent_roles:
            role_name = permanent_roles[new_level]
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                await member.add_roles(role)
                if notify_channel:
                    await notify_channel.send(
                        f"📸 {role_name} を獲得しました！"
                    )

        # =========================
        # 🔥 ランクロール（範囲判定版）
        # =========================

                    if notify_channel:
                        await notify_channel.send(
                            f"🏆 {role_name} ランクに昇格しました！"
                        )

                break

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
    data.setdefault(LAST_DECAY_KEY, "")

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

    # 月曜18:00〜18:01の間に実行（安全版）
    if now.weekday() == 0 and now.hour == 18 and now.minute < 2:

        data = load_data()

        if not data:
            return

        guild = bot.guilds[0]
        notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

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

        results_text = ""

        # Top3に付与
        for rank, (user_id, info) in enumerate(top3, start=1):

            role_name = weekly_roles.get(rank)
            role = discord.utils.get(guild.roles, name=role_name)
            member = guild.get_member(int(user_id))

            if role and member:
                await member.add_roles(role)

            weekly_xp = info.get("weekly_xp", 0)
            medal = ["🥇", "🥈", "🥉"][rank - 1]
            results_text += f"{medal} <@{user_id}> - {weekly_xp} XP\n"

        # 結果発表
        if notify_channel and results_text:
            embed = discord.Embed(
                title="🏆 週間ランキング結果発表！",
                description=results_text,
                color=discord.Color.gold()
            )
            embed.set_footer(text="次回は来週月曜18:00に更新！")

            await notify_channel.send(embed=embed)

        # weekly_xpリセット
        for user_id in data:
            data[user_id]["weekly_xp"] = 0

        save_data(data)
        
@tasks.loop(minutes=1)
async def weekly_mid_announcement():

    now = datetime.now(JST)

    # 毎日21:00〜21:01の間
    if now.hour == 21 and now.minute < 2:

        data = load_data()

        if not data:
            return

        guild = bot.guilds[0]
        notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

        # weekly_xp順にソート
        sorted_users = sorted(
            data.items(),
            key=lambda x: x[1].get("weekly_xp", 0),
            reverse=True
        )

        top5 = sorted_users[:5]

        if not top5:
            return

        description = ""

        medals = ["🥇", "🥈", "🥉", "④", "⑤"]

        for i, (user_id, info) in enumerate(top5):
            weekly_xp = info.get("weekly_xp", 0)
            description += f"{medals[i]} <@{user_id}> - {weekly_xp} XP\n"

        embed = discord.Embed(
            title="📊 週間ランキング中間発表",
            description=description,
            color=discord.Color.blue()
        )

        embed.set_footer(text="最終結果は月曜18:00に発表！")

        if notify_channel:
            await notify_channel.send(embed=embed)
        
@tasks.loop(hours=24)
async def decay_task():

    data = load_data()

    if not data:
        return

    now = datetime.now(timezone.utc)

    last_decay_str = data.get(LAST_DECAY_KEY)

    if last_decay_str:
        last_decay = datetime.strptime(last_decay_str, "%Y-%m-%d")
        if now - last_decay < timedelta(days=90):
            return

    guild = bot.guilds[0]
    notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

    results = ""

    for user_id, info in data.items():

        if not isinstance(info, dict):
            continue

        level = info.get("level", 1)

        decay_amount = int(level * DECAY_PERCENT)

        if decay_amount <= 0:
            continue

        new_level = max(1, level - decay_amount)

        info["level"] = new_level
        info["xp"] = 0
        
        member = guild.get_member(int(user_id))
if member:
        await update_rank_role(member, new_level)
        
    results += f"<@{user_id}> Lv{level} → Lv{new_level}\n"

    data[LAST_DECAY_KEY] = now.strftime("%Y-%m-%d")

    save_data(data)

    if notify_channel and results:
        embed = discord.Embed(
            title="⚔ レベル減衰が発生しました",
            description="全ユーザーのレベルが5%減少しました。\n\n" + results[:4000],
            color=discord.Color.red()
        )
        await notify_channel.send(embed=embed)
        
@bot.tree.command(name="sync_roles", description="全員のランクロールを再同期（管理者専用）")
async def sync_roles(interaction: discord.Interaction):

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("管理者専用コマンドです。", ephemeral=True)
        return

    await interaction.response.send_message("ロールを再同期中...")

    guild = interaction.guild
    data = load_data()

    for user_id, info in data.items():
        member = guild.get_member(int(user_id))
        if member:
            await update_rank_role(member, info.get("level", 1))

    await interaction.followup.send("✅ 全員のランクロールを再同期しました。")
        
# =========================
# 起動時
# =========================
@bot.event
async def on_ready():

    synced = await bot.tree.sync()
    print(f"{len(synced)}個のコマンドを同期しました")
    print(f"Logged in as {bot.user}")

    if not weekly_ranking_task.is_running():
        weekly_ranking_task.start()
        
    if not decay_task.is_running():
        decay_task.start()
    
    if not weekly_mid_announcement.is_running():
        weekly_mid_announcement.start()

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
