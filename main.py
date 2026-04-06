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
    data[user_id]["weekly_chat_xp"] = data[user_id].get("weekly_chat_xp", 0) + xp_gain

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
            # ミュート中は2XP、ミュート解除（発言中）は15XP
            is_muted = member.voice.self_mute or member.voice.mute
            base_xp = 2 if is_muted else 15
            gain = int(base_xp * boost["multiplier"])
            data[user_id]["xp"] += gain
            data[user_id]["weekly_xp"] += gain
            data[user_id]["weekly_vc_xp"] = data[user_id].get("weekly_vc_xp", 0) + gain

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
# ポモドーロタイマー
# =========================
POMODORO_WORK_MINUTES = 25
POMODORO_BREAK_MINUTES = 5
POMODORO_XP_REWARD = 50

# サーバーごとのタイマー状態
# { guild_id: PomodoroSession }
pomodoro_sessions = {}

class PomodoroSession:
    def __init__(self, vc_channel, notify_channel, started_by):
        self.vc_channel = vc_channel          # 対象VCチャンネル
        self.notify_channel = notify_channel  # 通知テキストチャンネル
        self.started_by = started_by          # 開始者
        self.participants = set()             # 参加者ID（途中参加含む）
        self.start_time = datetime.now(JST)
        self.running = True
        self.phase = "work"                   # "work" or "break"
        self.task = None                      # asyncioタスク

pomodoro_group = discord.app_commands.Group(name="pomodoro", description="ポモドーロタイマー")

@pomodoro_group.command(name="start", description="ポモドーロタイマーを開始（VCにいる必要があります）")
async def pomodoro_start(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    # すでに実行中チェック
    if guild_id in pomodoro_sessions and pomodoro_sessions[guild_id].running:
        await interaction.response.send_message(
            "⚠️ すでにタイマーが実行中です！ `/pomodoro stop` で停止できます。",
            ephemeral=True
        )
        return

    # VCにいるかチェック
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message(
            "⚠️ VCに参加してからコマンドを実行してください！",
            ephemeral=True
        )
        return

    vc_channel = interaction.user.voice.channel
    ch_id = get_level_channel_id(guild_id)
    notify_channel = interaction.guild.get_channel(ch_id) if ch_id else interaction.channel

    # セッション作成
    session = PomodoroSession(vc_channel, notify_channel, interaction.user)

    # 開始時点のVC参加者を登録
    for member in vc_channel.members:
        if not member.bot:
            session.participants.add(str(member.id))

    pomodoro_sessions[guild_id] = session

    await interaction.response.send_message(
        f"✅ ポモドーロタイマーを開始しました！",
        ephemeral=True
    )

    embed = discord.Embed(
        title="🍅 ポモドーロタイマー開始！",
        description=(
            f"**対象VC:** {vc_channel.mention}\n"
            f"**作業時間:** {POMODORO_WORK_MINUTES}分\n"
            f"**参加者:** {len(session.participants)}人\n\n"
            f"集中して頑張ろう！完走で **+{POMODORO_XP_REWARD}XP** 獲得！"
        ),
        color=discord.Color.red()
    )
    embed.set_footer(text=f"開始者: {interaction.user.display_name}")
    await notify_channel.send(embed=embed)

    # タイマータスクを開始
    session.task = asyncio.create_task(run_pomodoro(guild_id, session))

@pomodoro_group.command(name="stop", description="ポモドーロタイマーを手動停止")
@discord.app_commands.checks.has_permissions(administrator=True)
async def pomodoro_stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    if guild_id not in pomodoro_sessions or not pomodoro_sessions[guild_id].running:
        await interaction.response.send_message("現在タイマーは実行されていません。", ephemeral=True)
        return

    session = pomodoro_sessions[guild_id]
    session.running = False
    if session.task:
        session.task.cancel()
    del pomodoro_sessions[guild_id]

    await interaction.response.send_message("⏹️ タイマーを停止しました。", ephemeral=True)
    await session.notify_channel.send("⏹️ **ポモドーロタイマーが手動停止されました。**")

@pomodoro_group.command(name="status", description="現在のポモドーロタイマーの状態を確認")
async def pomodoro_status(interaction: discord.Interaction):
    guild_id = interaction.guild.id

    if guild_id not in pomodoro_sessions or not pomodoro_sessions[guild_id].running:
        await interaction.response.send_message("現在タイマーは実行されていません。", ephemeral=True)
        return

    session = pomodoro_sessions[guild_id]
    now = datetime.now(JST)
    elapsed = (now - session.start_time).seconds // 60
    work_min = POMODORO_WORK_MINUTES

    if session.phase == "work":
        remaining = max(0, work_min - elapsed)
        phase_label = "🍅 作業中"
    else:
        remaining = max(0, POMODORO_BREAK_MINUTES - elapsed)
        phase_label = "☕ 休憩中"

    participants_mention = " ".join(f"<@{uid}>" for uid in session.participants)

    embed = discord.Embed(
        title="🍅 ポモドーロ タイマー状況",
        color=discord.Color.orange()
    )
    embed.add_field(name="フェーズ", value=phase_label)
    embed.add_field(name="残り時間", value=f"約{remaining}分")
    embed.add_field(name="対象VC", value=session.vc_channel.mention, inline=False)
    embed.add_field(name=f"参加者（{len(session.participants)}人）", value=participants_mention or "なし", inline=False)
    await interaction.response.send_message(embed=embed)

bot.tree.add_command(pomodoro_group)

async def run_pomodoro(guild_id, session):
    """ポモドーロタイマーのメインループ"""
    try:
        # ===== 作業フェーズ（25分）=====
        session.phase = "work"

        # 1分ごとにVCの参加者を更新
        work_seconds = POMODORO_WORK_MINUTES * 60
        elapsed = 0
        while elapsed < work_seconds:
            await asyncio.sleep(10)
            elapsed += 10
            if not session.running:
                return

            # 途中参加者を追加
            for member in session.vc_channel.members:
                if not member.bot:
                    session.participants.add(str(member.id))

        # ===== 作業終了：完走者にXP付与 =====
        # VCに残っているメンバーのみ対象（2人以上いること）
        remaining_members = [m for m in session.vc_channel.members if not m.bot]
        remaining_ids = {str(m.id) for m in remaining_members}
        completed = session.participants & remaining_ids  # 最初から or 途中参加 かつ 残っている

        reward_text = ""
        if len(remaining_members) >= 2:
            data = load_data(guild_id)
            for uid in completed:
                if uid not in data:
                    data[uid] = {}
                data[uid].setdefault("xp", 0)
                data[uid].setdefault("level", 1)
                data[uid].setdefault("weekly_xp", 0)
                data[uid].setdefault("last_daily", "")
                data[uid].setdefault("login_streak", 0)
                data[uid]["xp"] += POMODORO_XP_REWARD
                data[uid]["weekly_xp"] += POMODORO_XP_REWARD
                reward_text += f"<@{uid}> "

                member = session.vc_channel.guild.get_member(int(uid))
                if member:
                    await check_level_up(member, data, uid)

            save_data(guild_id, data)
        else:
            reward_text = "（VCの人数が足りなかったためXP付与なし）"

        embed = discord.Embed(
            title="✅ ポモドーロ作業タイム終了！",
            description=(
                f"お疲れ様でした！\n\n"
                f"**完走者:** {reward_text}\n"
                f"**XP付与:** +{POMODORO_XP_REWARD}XP\n\n"
                f"☕ 休憩タイム（{POMODORO_BREAK_MINUTES}分）を開始します！"
            ),
            color=discord.Color.green()
        )
        await session.notify_channel.send(embed=embed)

        # ===== 休憩フェーズ（5分）=====
        session.phase = "break"
        await asyncio.sleep(POMODORO_BREAK_MINUTES * 60)

        if not session.running:
            return

        embed = discord.Embed(
            title="☕ 休憩終了！",
            description=(
                "休憩タイムが終わりました！\n"
                "次のポモドーロを始めるには `/pomodoro start` を使ってください！"
            ),
            color=discord.Color.blue()
        )
        await session.notify_channel.send(embed=embed)

    except asyncio.CancelledError:
        pass
    finally:
        if guild_id in pomodoro_sessions:
            del pomodoro_sessions[guild_id]

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

        # 前週データを保存してリセット
        for i, (uid, info) in enumerate(sorted_users, start=1):
            info["last_weekly_xp"] = info.get("weekly_xp", 0)
            info["last_weekly_rank"] = i

        for uid in data:
            if uid != LAST_DECAY_KEY:
                data[uid]["weekly_xp"] = 0
                data[uid]["weekly_chat_xp"] = 0
                data[uid]["weekly_vc_xp"] = 0
                data[uid]["weekly_active_days"] = []
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
            embed.set_footer(text="2時間ごとにダメージ報告あり")
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
