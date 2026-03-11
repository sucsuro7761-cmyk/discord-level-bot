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
LAST_DECAY_KEY = "last_decay”

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix=”!”, intents=intents)

DATA_FILE = “/data/levels.json”
LEVEL_CHANNEL_ID = 1477839103151177864

cooldowns = {}
vc_users = {}

# =========================

# XP BOOST SYSTEM

# =========================

XP_MULTIPLIER = 1
BOOST_ACTIVE = False

# =========================

# 二重実行防止フラグ

# =========================

_weekly_announced = None
_mid_announced_today = None

# =========================

# 週ボスシステム Config

# =========================

BOSS_FILE = “/data/boss.json”
BOSS_BASE_HP = 30000       # 初期HP
BOSS_HP_SCALE = 1.2        # クリアごとのHP倍率
BOSS_CLEAR_ROLE = “⚔️ボス討伐者”  # 討伐成功時に付与するロール名

def load_boss():
if not os.path.exists(BOSS_FILE):
return {“active”: False, “hp”: 0, “max_hp”: 0, “damage”: {}, “week”: 0, “cleared”: 0}
with open(BOSS_FILE, “r”) as f:
try:
return json.load(f)
except json.JSONDecodeError:
return {“active”: False, “hp”: 0, “max_hp”: 0, “damage”: {}, “week”: 0, “cleared”: 0}

def save_boss(boss):
os.makedirs(os.path.dirname(BOSS_FILE), exist_ok=True)
with open(BOSS_FILE, “w”) as f:
json.dump(boss, f, indent=4)

_boss_spawn_announced = None

# =========================

# Flask keep alive

# =========================

app = Flask(’’)

@app.route(’/’)
def home():
return “I’m alive!”

def run():
app.run(host=‘0.0.0.0’, port=5000)

def keep_alive():
t = Thread(target=run)
t.start()

# =========================

# Data read/write

# =========================

def load_data():
if not os.path.exists(DATA_FILE):
return {}
with open(DATA_FILE, “r”) as f:
try:
return json.load(f)
except json.JSONDecodeError:
return {}

def save_data(data):
os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
with open(DATA_FILE, “w”) as f:
json.dump(data, f, indent=4)

# =========================

# Rank definitions

# =========================

rank_roles = [
(1, 9, “MEMBER Lite”),
(10, 29, “MEMBER”),
(30, 49, “CORE”),
(50, 74, “SELECT”),
(75, 99, “PREMIUM”),
(100, 199, “VIP Lite”),
(200, 999, “VIP”),
(1000, 9999, “Legend”)
]

permanent_roles = {
3: “PHOTO+”
}

weekly_roles = {
1: “🥇週間王者”,
2: “🥈週間準王”,
3: “🥉週間三位”
}

# =========================

# Rank Role Updater

# =========================

async def update_rank_role(member, level):
guild = member.guild

```
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
```

# =========================

# Level-up check

# =========================

async def check_level_up(member, channel, data, user_id):
guild = member.guild
notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

```
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
```

# =========================

# Message XP

# =========================

@bot.event
async def on_message(message):
if message.author.bot:
return

```
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

xp_gain = int(random.randint(5, 20) * XP_MULTIPLIER)
data[user_id]["xp"] += xp_gain
data[user_id]["weekly_xp"] += xp_gain

await check_level_up(message.author, message.channel, data, user_id)
save_data(data)

# ボスが出現中ならダメージを与える
boss = load_boss()
if boss.get("active"):
    dmg = xp_gain
    boss["damage"][user_id] = boss["damage"].get(user_id, 0) + dmg
    boss["hp"] = max(0, boss["hp"] - dmg)
    if boss["hp"] <= 0:
        boss["active"] = False
        boss["cleared"] += 1
        save_boss(boss)
        await handle_boss_clear(message.guild, boss)
    else:
        save_boss(boss)

await bot.process_commands(message)
```

# =========================

# VC XP

# =========================

@bot.event
async def on_voice_state_update(member, before, after):
if member.bot:
return

```
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

        gain = int(10 * XP_MULTIPLIER)
        data[user_id]["xp"] += gain
        data[user_id]["weekly_xp"] += gain

        await check_level_up(member, member.guild.system_channel, data, user_id)
        save_data(data)

        # ボスが出現中ならVCXPでもダメージを与える
        boss = load_boss()
        if boss.get("active"):
            boss["damage"][user_id] = boss["damage"].get(user_id, 0) + gain
            boss["hp"] = max(0, boss["hp"] - gain)
            if boss["hp"] <= 0:
                boss["active"] = False
                boss["cleared"] += 1
                save_boss(boss)
                await handle_boss_clear(member.guild, boss)
            else:
                save_boss(boss)

if before.channel and not after.channel:
    vc_users[user_id] = False
```

# =========================

# /rank

# =========================

@bot.tree.command(name=“rank”, description=“自分のレベルを確認”)
async def rank(interaction: discord.Interaction):
await interaction.response.defer()
data = load_data()
user_id = str(interaction.user.id)
if user_id not in data:
await interaction.followup.send(“まだデータがありません！”)
return

```
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
```

# =========================

# /top

# =========================

@bot.tree.command(name=“top”, description=“XPランキングTOP10”)
async def top(interaction: discord.Interaction):
await interaction.response.defer()

```
users = load_data()

ranking = sorted(
    [(uid, info) for uid, info in users.items() if uid != LAST_DECAY_KEY],
    key=lambda x: (x[1].get("level", 1), x[1].get("xp", 0)),
    reverse=True
)

embed = discord.Embed(title="🏆 XPランキング TOP10", color=discord.Color.gold())
medals = ["🥇", "🥈", "🥉"]
text = ""

for i, (user_id, data) in enumerate(ranking[:10], start=1):
    level = data.get("level", 1)
    xp = data.get("xp", 0)
    icon = medals[i-1] if i <= 3 else f"{i}."
    text += f"{icon} <@{user_id}> | Lv{level} | {xp}XP\n"

embed.description = text
await interaction.followup.send(embed=embed)
```

# =========================

# /myxp

# =========================

@bot.tree.command(name=“myxp”, description=“自分のXPやレベルを確認”)
async def myxp(interaction: discord.Interaction):
data = load_data()
user_id = str(interaction.user.id)
if user_id not in data:
await interaction.response.send_message(“まだデータがありません！”)
return

```
info = data[user_id]
embed = discord.Embed(title=f"📊 {interaction.user.name} のデータ", color=discord.Color.green())
embed.add_field(name="レベル", value=f"Lv {info.get('level',1)}")
embed.add_field(name="XP", value=f"{info.get('xp',0)} XP")
embed.add_field(name="今週のXP", value=f"{info.get('weekly_xp',0)} XP")
embed.add_field(name="最終デイリーボーナス", value=info.get('last_daily','なし'))
await interaction.response.send_message(embed=embed)
```

# =========================

# /userdata（管理者用：特定ユーザーのデータ確認）

# =========================

@bot.tree.command(name=“userdata”, description=“ユーザーのデータを確認（管理者用）”)
@discord.app_commands.checks.has_permissions(administrator=True)
async def userdata(interaction: discord.Interaction, member: discord.Member):
data = load_data()
user_id = str(member.id)

```
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

# 現在のランク名を取得
current_rank = "なし"
for min_lv, max_lv, role_name in rank_roles:
    if min_lv <= level <= max_lv:
        current_rank = role_name
        break

# 今週のボスダメージ
boss = load_boss()
boss_dmg = boss.get("damage", {}).get(user_id, 0) if boss.get("active") else 0

embed = discord.Embed(
    title=f"🔍 {member.name} のデータ",
    color=discord.Color.orange()
)
embed.set_thumbnail(url=member.display_avatar.url)
embed.add_field(name="レベル", value=f"Lv {level}")
embed.add_field(name="ランク", value=current_rank)
embed.add_field(name="XP", value=f"{xp} / {required_xp}\n{bar} {int(progress*100)}%", inline=False)
embed.add_field(name="今週のXP", value=f"{info.get('weekly_xp', 0)} XP")
embed.add_field(name="最終デイリー", value=info.get("last_daily", "なし"))
embed.add_field(name="今週のボスダメージ", value=f"{boss_dmg} ダメージ")
await interaction.response.send_message(embed=embed, ephemeral=True)
```

@userdata.error
async def userdata_error(interaction: discord.Interaction, error):
if isinstance(error, discord.app_commands.MissingPermissions):
await interaction.response.send_message(“このコマンドは管理者のみ使用できます！”, ephemeral=True)

# =========================

# /alldata（管理者用：全ユーザーデータをCSV出力）

# =========================

@bot.tree.command(name=“alldata”, description=“全ユーザーデータをCSVで出力（管理者用）”)
@discord.app_commands.checks.has_permissions(administrator=True)
async def alldata(interaction: discord.Interaction):
await interaction.response.defer(ephemeral=True)
data = load_data()
guild = interaction.guild

```
output = io.StringIO()
writer = csv.writer(output)
writer.writerow(["UserID", "Username", "Level", "XP", "WeeklyXP", "LastDaily"])

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
        info.get("last_daily", "")
    ])

output.seek(0)
now_str = datetime.now(JST).strftime("%Y%m%d_%H%M")
filename = f"userdata_{now_str}.csv"
file = discord.File(
    fp=io.BytesIO(output.getvalue().encode("utf-8-sig")),  # Excel対応BOM付きUTF-8
    filename=filename
)
user_count = sum(1 for k in data if k != LAST_DECAY_KEY)
await interaction.followup.send(
    f"📊 全ユーザーデータです！（{user_count}人分）",
    file=file,
    ephemeral=True
)
```

@alldata.error
async def alldata_error(interaction: discord.Interaction, error):
if isinstance(error, discord.app_commands.MissingPermissions):
await interaction.response.send_message(“このコマンドは管理者のみ使用できます！”, ephemeral=True)

# =========================

# 週間ランキング（Final）

# =========================

JST = pytz.timezone(“Asia/Tokyo”)

@tasks.loop(minutes=1)
async def weekly_ranking_task():
global _weekly_announced
now = datetime.now(JST)
today = now.strftime(”%Y-%m-%d”)

```
if not (now.weekday() == 0 and now.hour == 18 and now.minute == 0):
    return
if _weekly_announced == today:
    return
_weekly_announced = today

data = load_data()
if not data:
    return

guild = bot.guilds[0]
notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

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
save_data(data)
```

# =========================

# XP BOOST TASK

# =========================

@tasks.loop(hours=24)
async def xp_boost_scheduler():
await bot.wait_until_ready()

```
global XP_MULTIPLIER
global BOOST_ACTIVE

channel = bot.get_channel(LEVEL_CHANNEL_ID)

morning_hour = random.randint(8, 11)
night_hour = random.randint(18, 22)
boosts = [morning_hour, night_hour]

for hour in boosts:
    now = datetime.now(JST)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)

    if now > target:
        continue

    wait = (target - now).total_seconds()
    await asyncio.sleep(wait)

    # ===== BOOST START =====
    BOOST_ACTIVE = True
    XP_MULTIPLIER = 3 if random.random() < 0.05 else 2

    if channel:
        await channel.send(
            f"🔥 **XP BOOST START!**\n"
            f"XPが **{XP_MULTIPLIER}倍** になりました！\n"
            f"1時間限定！"
        )

    await asyncio.sleep(3600)

    # ===== BOOST END =====
    XP_MULTIPLIER = 1
    BOOST_ACTIVE = False

    if channel:
        await channel.send("⏱ **XP BOOST 終了！**")
```

# =========================

# 週間ランキング中間発表（毎日21時）

# =========================

@tasks.loop(minutes=1)
async def weekly_mid_announcement():
global _mid_announced_today
now = datetime.now(JST)
today = now.strftime(”%Y-%m-%d”)

```
if not (now.hour == 21 and now.minute == 0):
    return
if _mid_announced_today == today:
    return
_mid_announced_today = today

data = load_data()
if not data:
    return

guild = bot.guilds[0]
notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

sorted_users = sorted(
    [(uid, info) for uid, info in data.items() if uid != LAST_DECAY_KEY],
    key=lambda x: x[1].get("weekly_xp", 0),
    reverse=True
)

top5 = sorted_users[:5]
desc = ""
medals = ["🥇", "🥈", "🥉", "④", "⑤"]

for i, (uid, info) in enumerate(top5):
    desc += f"{medals[i]} <@{uid}> - {info.get('weekly_xp',0)} XP\n"

if notify_channel:
    embed = discord.Embed(
        title="📊 週間ランキング中間発表",
        description=desc,
        color=discord.Color.blue()
    )
    embed.set_footer(text="最終結果は月曜18:00に発表！")
    await notify_channel.send(embed=embed)
```

# =========================

# 3ヶ月レベル減衰

# =========================

@tasks.loop(hours=24)
async def decay_task():
data = load_data()
if not data:
return

```
now = datetime.now(timezone.utc)
last_str = data.get(LAST_DECAY_KEY, "")

if not last_str:
    data[LAST_DECAY_KEY] = now.strftime("%Y-%m-%d")
    save_data(data)
    return

last_dt = datetime.strptime(last_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
if (now - last_dt).days < 90:
    return

guild = bot.guilds[0]
notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

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
save_data(data)

if notify_channel and results:
    embed = discord.Embed(
        title="⚔ レベル減衰が発生しました",
        description=results,
        color=discord.Color.red()
    )
    await notify_channel.send(embed=embed)
```

# =========================

# 週ボス：討伐成功処理

# =========================

async def handle_boss_clear(guild, boss):
global XP_MULTIPLIER, BOOST_ACTIVE

```
notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)

# 討伐者全員にロール付与
role = discord.utils.get(guild.roles, name=BOSS_CLEAR_ROLE)
for uid, dmg in boss["damage"].items():
    if dmg <= 0:
        continue
    member = guild.get_member(int(uid))
    if not member:
        continue
    if role:
        await member.add_roles(role)

# ダメージTOP3
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

asyncio.create_task(boss_clear_boost(notify_channel))
```

# =========================

# 週ボス：討伐ブースト（次の月曜6時まで）

# =========================

async def boss_clear_boost(notify_channel):
global XP_MULTIPLIER, BOOST_ACTIVE
BOOST_ACTIVE = True
XP_MULTIPLIER = 2
if notify_channel:
await notify_channel.send(“🔥 **討伐記念 XP 2倍ブースト開始！** 次のボス出現まで継続！”)

```
now = datetime.now(JST)
days_until_monday = (7 - now.weekday()) % 7 or 7
next_monday = (now + timedelta(days=days_until_monday)).replace(
    hour=6, minute=0, second=0, microsecond=0
)
wait_seconds = (next_monday - now).total_seconds()
await asyncio.sleep(wait_seconds)

XP_MULTIPLIER = 1
BOOST_ACTIVE = False
if notify_channel:
    await notify_channel.send("⏱ **討伐ブースト終了！** 新しいボスが出現しました！")
```

# =========================

# 週ボス：出現タスク（月曜6時）

# =========================

@tasks.loop(minutes=1)
async def boss_spawn_task():
global _boss_spawn_announced
now = datetime.now(JST)
today = now.strftime(”%Y-%m-%d”)

```
if not (now.weekday() == 0 and now.hour == 6 and now.minute <= 2):
    return
if _boss_spawn_announced == today:
    return
_boss_spawn_announced = today

boss = load_boss()

if boss.get("active"):
    guild = bot.guilds[0]
    notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)
    if notify_channel:
        await notify_channel.send("💀 **ボスは討伐されませんでした...**\n今週こそリベンジだ！")

global XP_MULTIPLIER, BOOST_ACTIVE
XP_MULTIPLIER = 1
BOOST_ACTIVE = False

cleared = boss.get("cleared", 0)
new_hp = int(BOSS_BASE_HP * (BOSS_HP_SCALE ** cleared))

new_boss = {
    "active": True,
    "hp": new_hp,
    "max_hp": new_hp,
    "damage": {},
    "week": boss.get("week", 0) + 1,
    "cleared": cleared
}
save_boss(new_boss)

guild = bot.guilds[0]
notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)
if notify_channel:
    embed = discord.Embed(
        title=f"👹 週ボス出現！ Week {new_boss['week']}",
        description=(
            f"ボスが現れた！今週中に倒せ！\n\n"
            f"メッセージを送るだけで自動攻撃！\n"
            f"討伐成功で特別ロールをGET！"
        ),
        color=discord.Color.red()
    )
    embed.add_field(name="❤️ HP", value=f"{new_hp:,} / {new_hp:,}")
    embed.add_field(name="⚔️ 攻撃方法", value="メッセージ送信 or VC参加で自動攻撃！")
    embed.add_field(name="🎁 討伐報酬", value="次のボス出現まで XP 2倍ブースト ＋ 特別ロール")
    embed.set_footer(text="2時間ごとにダメージ報告あり")
    await notify_channel.send(embed=embed)
```

# =========================

# 週ボス：2時間ごとダメージ報告

# =========================

@tasks.loop(hours=2)
async def boss_damage_report():
await bot.wait_until_ready()

```
boss = load_boss()
if not boss.get("active"):
    return

guild = bot.guilds[0]
notify_channel = guild.get_channel(LEVEL_CHANNEL_ID)
if not notify_channel:
    return

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

embed = discord.Embed(
    title="⚔️ 週ボス ダメージレポート",
    color=discord.Color.orange()
)
embed.add_field(
    name="❤️ ボスHP",
    value=f"{bar} {percent}%\n{current_hp:,} / {max_hp:,}",
    inline=False
)
embed.add_field(name="🏆 ダメージTOP3", value=top_text, inline=False)
embed.set_footer(text="メッセージを送って攻撃しよう！")
await notify_channel.send(embed=embed)
```

# =========================

# /boss コマンド（ボス状況確認）

# =========================

@bot.tree.command(name=“boss”, description=“今週のボス状況を確認”)
async def boss_status(interaction: discord.Interaction):
boss = load_boss()

```
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
embed.add_field(
    name="❤️ ボスHP",
    value=f"{bar} {percent}%\n{current_hp:,} / {max_hp:,}",
    inline=False
)
embed.add_field(name="🏆 ダメージTOP3", value=top_text, inline=False)
embed.add_field(name="⚔️ あなたのダメージ", value=f"{my_dmg}ダメージ", inline=False)
await interaction.response.send_message(embed=embed)
```

# =========================

# サーバー参加時：ロール＆チャンネル自動作成

# =========================

@bot.event
async def on_guild_join(guild):

```
roles_to_create = [
    {"name": "MEMBER Lite",  "color": discord.Color.from_rgb(153, 153, 153)},
    {"name": "MEMBER",       "color": discord.Color.from_rgb(59,  165,  93)},
    {"name": "CORE",         "color": discord.Color.from_rgb(31,  139,  76)},
    {"name": "SELECT",       "color": discord.Color.from_rgb(78,   93, 148)},
    {"name": "PREMIUM",      "color": discord.Color.from_rgb(255, 168,   0)},
    {"name": "VIP Lite",     "color": discord.Color.from_rgb(163,  73, 164)},
    {"name": "VIP",          "color": discord.Color.from_rgb(113,  54, 138)},
    {"name": "Legend",       "color": discord.Color.from_rgb(255, 215,   0)},  # 金色
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

if notify_channel:
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
            "`/userdata` - ユーザーデータ確認（管理者）\n"
            "`/alldata` - 全データCSV出力（管理者）"
        )
    )
    embed.set_footer(text=f"通知チャンネルID: {notify_channel.id}（必要に応じてコードのLEVEL_CHANNEL_IDを変更してください）")
    await notify_channel.send(embed=embed)
```

# =========================

# 起動時

# =========================

@bot.event
async def on_ready():
synced = await bot.tree.sync()
print(f”{len(synced)} commands synced | Logged in as {bot.user}”)

```
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

data = load_data()

for guild in bot.guilds:
    for user_id, info in data.items():
        if user_id == LAST_DECAY_KEY:
            continue
        member = guild.get_member(int(user_id))
        if member:
            await update_rank_role(member, info.get("level", 1))
            await asyncio.sleep(0.5)
```

# =========================

# Run

# =========================

if **name** == “**main**”:
keep_alive()
token = os.environ.get(“TOKEN”)
if token:
bot.run(token)
else:
print(“Error: TOKEN not set”)