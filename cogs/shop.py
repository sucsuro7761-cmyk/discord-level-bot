import asyncio
import math
import random
import time
from datetime import datetime

import discord
from discord.ext import commands

from utils.config import get_level_channel_id, load_shop_log, resolve_display_title, save_shop_log
from utils.constants import COIN_DAILY_CAP, JST, LAST_DECAY_KEY, SHOP_ITEMS, title_display
from utils.data import (
    add_mission_progress,
    add_timed_buff,
    cleanup_expired_buffs,
    ensure_user_data,
    get_mission_progress,
    get_today_mission,
    load_boss,
    load_data,
    now_ts,
    save_data,
    spend_coins,
)

# =========================
# 投資パック定数
# =========================
INVEST_TABLE = [
    (0.5, 0.40, "大暴落…",        "📉"),
    (1.0, 0.35, "元本割れなし",   "📊"),
    (1.5, 0.20, "安定した利益！", "📊"),
    (3.0, 0.05, "爆益！！",       "📈"),
]
INVEST_MIN      = 500
INVEST_MAX      = 50000
INVEST_STEP     = 500
INVEST_DURATION = 24 * 60 * 60

# =========================
# ROYAL PASS 定数
# =========================
ROYAL_PASS_ROLE         = "👑 ROYAL PASS"
ROYAL_PASS_XP_BONUS     = 1.2
ROYAL_PASS_DAILY_BONUS  = 1.5
ROYAL_PASS_DURATION     = 7 * 24 * 60 * 60


def draw_mystery_box():
    r = random.random()
    if r < 0.25:
        return "lose", random.choice(["small_coins", "big_lose", "curse", "msg1", "msg2"])
    if r < 0.95:
        reward_type = random.choice(["xp_boost", "attack_up", "coins", "login_bonus_2x"])
        if reward_type == "xp_boost":
            return "normal", {"type": "xp_boost", "duration": 30 * 60, "value": 2.0}
        elif reward_type == "attack_up":
            return "normal", {"type": "attack_up", "duration": 15 * 60, "value": 1.2}
        elif reward_type == "coins":
            return "normal", {"type": "coins", "amount": random.randint(300, 800)}
        else:
            return "normal", {"type": "login_bonus_2x"}
    rare_type = random.choice(["coins", "boss_slayer", "rare_role"])
    if rare_type == "coins":
        return "rare", {"type": "coins", "amount": 5000}
    elif rare_type == "boss_slayer":
        return "rare", {"type": "boss_slayer", "duration": 30 * 60, "value": 1.5}
    else:
        return "rare", {"type": "rare_role"}


def draw_investment():
    r = random.random()
    cumulative = 0.0
    for multiplier, prob, label, emoji in INVEST_TABLE:
        cumulative += prob
        if r < cumulative:
            return multiplier, label, emoji
    return 1.0, "元本割れなし", "📊"


async def notify_buff_end(guild, user_name: str, item_name: str, duration_seconds: int):
    await asyncio.sleep(duration_seconds)
    ch_id = get_level_channel_id(guild.id)
    notify_ch = guild.get_channel(ch_id) if ch_id else None
    if notify_ch:
        await notify_ch.send(f"⏱ **{user_name}** の **{item_name}** の効果が終了しました。")


class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._openbox_cooldowns: dict[str, float] = {}
        self._chest_cooldowns: dict[str, float] = {}

    # =========================
    # /coins
    # =========================
    @discord.app_commands.command(name="coins", description="所持コインを確認します")
    async def coins(self, interaction: discord.Interaction):
        data = load_data(interaction.guild.id)
        user_id = str(interaction.user.id)
        info = ensure_user_data(data, user_id)
        save_data(interaction.guild.id, data)

        embed = discord.Embed(title="💰 所持コイン", color=discord.Color.gold())
        embed.add_field(name="現在の所持コイン", value=f"{info.get('coins', 0):,}コイン", inline=False)
        embed.add_field(name="今日の獲得量", value=f"{info.get('coin_daily_earned', 0):,} / {COIN_DAILY_CAP:,}コイン", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =========================
    # /buffs
    # =========================
    @discord.app_commands.command(name="buffs", description="有効なアイテム効果を確認します")
    async def buffs(self, interaction: discord.Interaction):
        data = load_data(interaction.guild.id)
        user_id = str(interaction.user.id)
        info = ensure_user_data(data, user_id)
        cleanup_expired_buffs(info)
        save_data(interaction.guild.id, data)

        if not info.get("buffs"):
            await interaction.response.send_message("現在有効なバフはありません。", ephemeral=True)
            return

        lines = []
        current = now_ts()
        for buff_type, buff in info["buffs"].items():
            remain = max(0, buff.get("expires_at", 0) - current)
            minutes = math.ceil(remain / 60)
            item = SHOP_ITEMS.get(buff.get("item_id"), {})
            lines.append(f"**{item.get('name', buff_type)}**：残り約{minutes}分")

        embed = discord.Embed(title="✨ 有効なバフ", description="\n".join(lines), color=discord.Color.blue())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =========================
    # /shop
    # =========================
    @discord.app_commands.command(name="shop", description="ショップの商品一覧を表示します")
    async def shop(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛒 ショップ",
            description="購入するには `/buy item_id` を使ってください。商品IDは英字のまま入力します。",
            color=discord.Color.green()
        )
        for item_id, item in SHOP_ITEMS.items():
            embed.add_field(
                name=f"{item['name']}｜{item['price']:,}コイン",
                value=f"商品ID: `{item_id}`\n{item['description']}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =========================
    # /buy
    # =========================
    @discord.app_commands.command(name="buy", description="ショップの商品を購入します")
    @discord.app_commands.describe(item_id="購入する商品のID（/shop で確認）")
    async def buy(self, interaction: discord.Interaction, item_id: str):
        item_id = item_id.lower().strip()

        if item_id not in SHOP_ITEMS:
            await interaction.response.send_message(
                "その商品IDは存在しません。`/shop` で商品一覧を確認してください。",
                ephemeral=True
            )
            return

        data = load_data(interaction.guild.id)
        user_id = str(interaction.user.id)
        info = ensure_user_data(data, user_id)
        item = SHOP_ITEMS[item_id]

        if item_id == "mystery_box":
            await interaction.response.send_message("🎁 ミステリーボックスは `/openbox` コマンドで購入・使用できます！", ephemeral=True)
            return
        if item_id == "investment_pack":
            await interaction.response.send_message("📈 投資パックは `/invest` コマンドで購入・使用できます！", ephemeral=True)
            return
        if item_id == "royal_pass":
            await interaction.response.send_message("👑 ROYAL PASSは `/buyroyal` コマンドで購入できます！", ephemeral=True)
            return

        if not spend_coins(data, user_id, item["price"], f"buy_{item_id}"):
            await interaction.response.send_message(
                f"コインが足りません。\n必要: **{item['price']:,}コイン**\n所持: **{info.get('coins', 0):,}コイン**",
                ephemeral=True
            )
            return

        if item_id == "rankdown_shield":
            info.setdefault("buffs", {})
            info["buffs"]["rankdown_shield"] = {"active": True}
        else:
            add_timed_buff(info, item["buff_type"], item["value"], item["duration"], item_id)

        info["weekly_coins_spent"] = info.get("weekly_coins_spent", 0) + item["price"]

        shop_log = load_shop_log(interaction.guild.id)
        week_key = datetime.now(JST).strftime("%Y-W%W")
        shop_log.setdefault(week_key, {})
        shop_log[week_key][item_id] = shop_log[week_key].get(item_id, 0) + 1
        save_shop_log(interaction.guild.id, shop_log)
        save_data(interaction.guild.id, data)

        duration_min = item["duration"] // 60
        await interaction.response.send_message(
            f"✅ **{item['name']}** を購入しました！\n効果: {item['description']}",
            ephemeral=True
        )

        ch_id = get_level_channel_id(interaction.guild.id)
        notify_ch = interaction.guild.get_channel(ch_id) if ch_id else None
        if notify_ch:
            await notify_ch.send(
                f"✨ **{interaction.user.display_name}** が **{item['name']}** を使用しました！"
                f"（有効時間　{duration_min}分）"
            )

        asyncio.create_task(notify_buff_end(
            interaction.guild, interaction.user.display_name,
            item["name"], item["duration"]
        ))

    # =========================
    # /chest
    # =========================
    @discord.app_commands.command(name="chest", description="ランダム宝箱を開ける（1時間に1回）")
    async def chest(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id  = str(interaction.user.id)
        ck  = f"{guild_id}:{user_id}"
        now = time.time()

        if ck in self._chest_cooldowns and now - self._chest_cooldowns[ck] < 3600:
            remaining = int(3600 - (now - self._chest_cooldowns[ck]))
            mins = remaining // 60
            secs = remaining % 60
            await interaction.response.send_message(
                f"⏳ 宝箱のクールダウン中です。あと **{mins}分{secs}秒** お待ちください！",
                ephemeral=True
            )
            return

        self._chest_cooldowns[ck] = now
        data = load_data(guild_id)
        info = ensure_user_data(data, user_id)

        today_earned = info.get("coin_daily_earned", 0)
        if today_earned >= COIN_DAILY_CAP:
            await interaction.response.send_message(
                f"💸 今日のコイン獲得上限（{COIN_DAILY_CAP:,}コイン）に達しています。明日またどうぞ！",
                ephemeral=True
            )
            return

        coin_gain = random.randint(10, 100)
        coin_gain = min(coin_gain, COIN_DAILY_CAP - today_earned)
        info["coins"]               = info.get("coins", 0) + coin_gain
        info["coin_daily_earned"]   = today_earned + coin_gain
        info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + coin_gain

        add_mission_progress(info, "chest_count", 1)
        save_data(guild_id, data)

        embed = discord.Embed(
            title="📦 宝箱を開けた！",
            description=f"{interaction.user.mention} が宝箱を開けました！\n💰 **+{coin_gain:,}コイン** 獲得！",
            color=discord.Color.gold()
        )
        embed.set_footer(text="次の宝箱は1時間後に開けられます")
        await interaction.response.send_message(embed=embed)

    # =========================
    # /dailymission
    # =========================
    @discord.app_commands.command(name="dailymission", description="今日のデイリーミッションを確認・受け取る")
    async def dailymission(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id  = str(interaction.user.id)
        data     = load_data(guild_id)
        info     = ensure_user_data(data, user_id)

        today    = datetime.now(JST).strftime("%Y-%m-%d")
        mission  = get_today_mission()
        m_type   = mission["type"]
        m_goal   = mission["goal"]
        m_reward = mission["reward"]
        m_label  = mission["label"]

        if info.get("daily_mission_claimed") == today:
            await interaction.response.send_message(
                "✅ 今日のデイリーミッションは既に受け取り済みです！明日また来てね。",
                ephemeral=True
            )
            return

        progress = get_mission_progress(info, m_type)
        achieved = progress >= m_goal

        if m_type == "boss_damage" and not achieved:
            boss = load_boss(guild_id)
            if not boss.get("active"):
                achieved = True
                m_label = "⚔️ ボスに300ダメージ与える（今週のボスは討伐済み！）"

        if not achieved:
            bar_filled = int((min(progress, m_goal) / m_goal) * 10)
            bar = "█" * bar_filled + "░" * (10 - bar_filled)
            embed = discord.Embed(
                title="🎯 今日のデイリーミッション",
                description=(
                    f"**{m_label}**\n\n"
                    f"進捗：`{bar}` {progress:.1f} / {m_goal}\n"
                    f"💰 達成報酬：**{m_reward}コイン**\n\n"
                    f"⏳ まだ未達成です。頑張ろう！"
                ),
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
            return

        today_earned = info.get("coin_daily_earned", 0)
        if today_earned >= COIN_DAILY_CAP:
            await interaction.response.send_message(
                f"💸 今日のコイン獲得上限（{COIN_DAILY_CAP:,}コイン）に達しています。",
                ephemeral=True
            )
            return

        reward_coins = min(m_reward, COIN_DAILY_CAP - today_earned)
        info["coins"]               = info.get("coins", 0) + reward_coins
        info["coin_daily_earned"]   = today_earned + reward_coins
        info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + reward_coins
        info["daily_mission_claimed"] = today
        info["weekly_missions_completed"] = info.get("weekly_missions_completed", 0) + 1
        save_data(guild_id, data)

        embed = discord.Embed(
            title="🎯 デイリーミッション達成！",
            description=(
                f"**{m_label}**\n\n"
                f"✅ ミッション達成！\n"
                f"💰 **+{reward_coins:,}コイン** 獲得！"
            ),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    # =========================
    # /openbox
    # =========================
    @discord.app_commands.command(name="openbox", description="🎁 ミステリーボックスを購入して開ける（3000コイン）")
    async def openbox(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id  = str(interaction.user.id)
        ck  = f"{guild_id}:{user_id}"
        now = time.time()

        if ck in self._openbox_cooldowns and now - self._openbox_cooldowns[ck] < 30:
            remain = int(30 - (now - self._openbox_cooldowns[ck]))
            await interaction.response.send_message(f"⏳ あと **{remain}秒** 待ってから開けてください！", ephemeral=True)
            return

        data  = load_data(guild_id)
        info  = ensure_user_data(data, user_id)
        price = SHOP_ITEMS["mystery_box"]["price"]

        if not spend_coins(data, user_id, price, "buy_mystery_box"):
            await interaction.response.send_message(
                f"コインが足りません。\n必要: **{price:,}コイン** ／ 所持: **{info.get('coins', 0):,}コイン**",
                ephemeral=True
            )
            return

        self._openbox_cooldowns[ck] = now
        info["weekly_coins_spent"] = info.get("weekly_coins_spent", 0) + price
        shop_log = load_shop_log(guild_id)
        week_key = datetime.now(JST).strftime("%Y-W%W")
        shop_log.setdefault(week_key, {})
        shop_log[week_key]["mystery_box"] = shop_log[week_key].get("mystery_box", 0) + 1
        save_shop_log(guild_id, shop_log)

        rarity, reward = draw_mystery_box()

        if rarity == "lose":
            LOSE_PATTERNS = {
                "small_coins": ("空箱だった…\n\nせめてもの慰めに 💰 **+50コイン** をどうぞ。",         50),
                "big_lose":    ("完全な空箱だった…！\n\n慰めに 💰 **+100コイン** をどうぞ。",          100),
                "curse":       ("💀 **呪いのボックス！！**\n\nXPが **-50** 削られてしまった…！",       0),
                "msg1":        ("ゴロゴロ…カラン…\n\n**何も入っていなかった。** 💰 **+100コイン**",    100),
                "msg2":        ("箱を開けたら説明書だけ入ってた。\n\n💰 **+100コイン** で許して。",     100),
            }
            msg, coin_back = LOSE_PATTERNS[reward]
            if reward == "curse":
                info["xp"] = max(0, info.get("xp", 0) - 50)
            else:
                info["coins"]               = info.get("coins", 0) + coin_back
                info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + coin_back

            save_data(guild_id, data)
            embed = discord.Embed(
                title="📦 ミステリーボックスを開けた！",
                description=msg,
                color=discord.Color.greyple()
            )
            embed.set_footer(text="次こそはレアが出るかも…？")
            await interaction.response.send_message(embed=embed)
            return

        embed_color = discord.Color.gold() if rarity == "normal" else discord.Color.from_rgb(255, 50, 200)
        reward_text = ""

        if reward["type"] == "coins":
            amount = reward["amount"]
            info["coins"]               = info.get("coins", 0) + amount
            info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + amount
            reward_text = f"💰 **+{amount:,}コイン** 獲得！"
        elif reward["type"] == "login_bonus_2x":
            info["login_bonus_2x"] = True
            reward_text = "🎁 **翌日のログインボーナス2倍チケット** 獲得！\n次回ログイン時に自動適用されます。"
        elif reward["type"] == "xp_boost":
            add_timed_buff(info, "xp_multiplier", reward["value"], reward["duration"], "mystery_xp_boost")
            reward_text = f"⚡ **XPブースト {reward['value']}倍**（30分）獲得！"
        elif reward["type"] == "attack_up":
            add_timed_buff(info, "damage_multiplier", reward["value"], reward["duration"], "mystery_attack")
            reward_text = f"⚔️ **攻撃力アップ {reward['value']}倍**（15分）獲得！"
        elif reward["type"] == "boss_slayer":
            add_timed_buff(info, "boss_damage_multiplier", reward["value"], reward["duration"], "mystery_boss")
            reward_text = f"🗡️ **ボス特効 {reward['value']}倍**（30分）獲得！"
        elif reward["type"] == "rare_role":
            role_name = "🎁 ミステリー当選者"
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if not role:
                try:
                    role = await interaction.guild.create_role(
                        name=role_name, color=discord.Color.from_rgb(255, 215, 0), reason="ミステリーボックス レア称号"
                    )
                except discord.Forbidden:
                    role = None
            if role:
                try:
                    await interaction.user.add_roles(role)
                except discord.Forbidden:
                    pass
            reward_text = f"👑 **レア称号「{role_name}」** を獲得！"

        save_data(guild_id, data)

        title  = "🌟✨ レア報酬！！ ✨🌟" if rarity == "rare" else "📦 ミステリーボックスを開けた！"
        prefix = "🎊 おめでとうございます！！\n\n" if rarity == "rare" else ""
        embed  = discord.Embed(title=title, description=f"{prefix}{reward_text}", color=embed_color)
        await interaction.response.send_message(embed=embed)

    # =========================
    # /invest
    # =========================
    @discord.app_commands.command(name="invest", description="📈 コインを投資！500コイン単位で設定可（24時間後に /claiminvest で回収）")
    @discord.app_commands.describe(amount="投資額（500コイン単位・500〜50,000コイン）")
    async def invest(self, interaction: discord.Interaction, amount: int = 3000):
        guild_id = interaction.guild.id
        user_id  = str(interaction.user.id)
        data = load_data(guild_id)
        info = ensure_user_data(data, user_id)

        if amount % INVEST_STEP != 0:
            await interaction.response.send_message(
                f"❌ 投資額は **{INVEST_STEP:,}コイン単位** で指定してください。\n例：500・1000・3000・10000",
                ephemeral=True
            )
            return
        if amount < INVEST_MIN or amount > INVEST_MAX:
            await interaction.response.send_message(
                f"❌ 投資額は **{INVEST_MIN:,}〜{INVEST_MAX:,}コイン** の範囲で指定してください。",
                ephemeral=True
            )
            return

        inv = info.get("investment")
        if inv:
            claim_at = inv["invested_at"] + INVEST_DURATION
            if time.time() < claim_at:
                remain = int(claim_at - time.time())
                h, m = divmod(remain // 60, 60)
                await interaction.response.send_message(
                    f"📊 現在投資中です。回収可能まで残り **{h}時間{m}分**\n`/claiminvest` で回収してください。",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "📊 前回の投資がまだ回収されていません。\n`/claiminvest` で回収してからもう一度どうぞ！",
                    ephemeral=True
                )
            return

        if not spend_coins(data, user_id, amount, "buy_investment_pack"):
            await interaction.response.send_message(
                f"コインが足りません。\n必要: **{amount:,}コイン** ／ 所持: **{info.get('coins', 0):,}コイン**",
                ephemeral=True
            )
            return

        info["weekly_coins_spent"] = info.get("weekly_coins_spent", 0) + amount
        info["investment"] = {"amount": amount, "invested_at": time.time()}

        add_mission_progress(info, "invest_done", 1)
        shop_log = load_shop_log(guild_id)
        week_key = datetime.now(JST).strftime("%Y-W%W")
        shop_log.setdefault(week_key, {})
        shop_log[week_key]["investment_pack"] = shop_log[week_key].get("investment_pack", 0) + 1
        save_shop_log(guild_id, shop_log)
        save_data(guild_id, data)

        embed = discord.Embed(
            title="📈 投資完了！",
            description=(
                f"**{amount:,}コイン** を投資しました！\n\n"
                f"24時間後に `/claiminvest` で結果を確認してください。\n"
                f"運命は…神のみぞ知る🎲"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"投資額：{amount:,}コイン ／ 期待値：約×1.025")
        await interaction.response.send_message(embed=embed)

    # =========================
    # /investstatus
    # =========================
    @discord.app_commands.command(name="investstatus", description="現在の投資状況を確認する")
    async def investstatus(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id  = str(interaction.user.id)
        data = load_data(guild_id)
        info = ensure_user_data(data, user_id)
        inv  = info.get("investment")

        if not inv:
            await interaction.response.send_message(
                "📊 投資中のパックはありません。`/invest` で投資を始めましょう！", ephemeral=True
            )
            return

        claim_at  = inv["invested_at"] + INVEST_DURATION
        claimable = time.time() >= claim_at

        if claimable:
            status_text = "✅ **回収可能です！** `/claiminvest` で回収してください。"
            color = discord.Color.green()
        else:
            remain = int(claim_at - time.time())
            h, m   = divmod(remain // 60, 60)
            status_text = f"⏳ 回収まで残り **{h}時間{m}分**"
            color = discord.Color.orange()

        embed = discord.Embed(title="📊 投資状況", color=color)
        embed.add_field(name="投資額", value=f"{inv['amount']:,}コイン", inline=True)
        embed.add_field(name="状態",   value=status_text, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =========================
    # /claiminvest
    # =========================
    @discord.app_commands.command(name="claiminvest", description="投資パックの結果を回収する")
    async def claiminvest(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id  = str(interaction.user.id)
        data = load_data(guild_id)
        info = ensure_user_data(data, user_id)
        inv  = info.get("investment")

        if not inv:
            await interaction.response.send_message(
                "📊 投資中のパックがありません。`/invest` で投資を始めましょう！", ephemeral=True
            )
            return

        if time.time() < inv["invested_at"] + INVEST_DURATION:
            remain = int(inv["invested_at"] + INVEST_DURATION - time.time())
            h, m   = divmod(remain // 60, 60)
            await interaction.response.send_message(
                f"⏳ まだ回収できません。あと **{h}時間{m}分** 待ってください！", ephemeral=True
            )
            return

        amount               = inv["amount"]
        multiplier, label, emoji = draw_investment()
        payout               = int(amount * multiplier)
        profit               = payout - amount

        info["coins"]               = info.get("coins", 0) + payout
        info["weekly_coins_earned"] = info.get("weekly_coins_earned", 0) + payout
        info["investment"]          = None
        save_data(guild_id, data)

        if multiplier >= 3.0:
            color = discord.Color.from_rgb(255, 215, 0)
        elif multiplier >= 1.5:
            color = discord.Color.green()
        elif multiplier == 1.0:
            color = discord.Color.blue()
        else:
            color = discord.Color.red()

        profit_text = f"+{profit:,}" if profit >= 0 else f"{profit:,}"
        embed = discord.Embed(
            title=f"{emoji} {label}",
            description=(
                f"投資額：**{amount:,}コイン**\n"
                f"倍率：**×{multiplier}**\n"
                f"回収額：**{payout:,}コイン**（{profit_text}コイン）"
            ),
            color=color
        )
        await interaction.response.send_message(embed=embed)

    # =========================
    # /buyroyal
    # =========================
    @discord.app_commands.command(name="buyroyal", description="👑 ROYAL PASSを購入する（50,000コイン・7日間）")
    async def buyroyal(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        user_id  = str(interaction.user.id)
        data     = load_data(guild_id)
        info     = ensure_user_data(data, user_id)
        price    = SHOP_ITEMS["royal_pass"]["price"]

        cleanup_expired_buffs(info)
        if info.get("buffs", {}).get("royal_pass"):
            expires_at = info["buffs"]["royal_pass"].get("expires_at", 0)
            remain     = max(0, int(expires_at - time.time()))
            d, r       = divmod(remain, 86400)
            h, _       = divmod(r, 3600)
            await interaction.response.send_message(
                f"👑 ROYAL PASSは既に有効です！\n残り **{d}日{h}時間**",
                ephemeral=True
            )
            return

        if not spend_coins(data, user_id, price, "buy_royal_pass"):
            await interaction.response.send_message(
                f"コインが足りません。\n必要: **{price:,}コイン** ／ 所持: **{info.get('coins', 0):,}コイン**",
                ephemeral=True
            )
            return

        add_timed_buff(info, "royal_pass",       True,                   ROYAL_PASS_DURATION, "royal_pass")
        add_timed_buff(info, "xp_multiplier",    ROYAL_PASS_XP_BONUS,   ROYAL_PASS_DURATION, "royal_pass")
        add_timed_buff(info, "daily_multiplier", ROYAL_PASS_DAILY_BONUS, ROYAL_PASS_DURATION, "royal_pass")

        role = discord.utils.get(interaction.guild.roles, name=ROYAL_PASS_ROLE)
        if not role:
            try:
                role = await interaction.guild.create_role(
                    name=ROYAL_PASS_ROLE,
                    color=discord.Color.from_rgb(255, 215, 0),
                    reason="ROYAL PASS購入"
                )
            except (discord.Forbidden, discord.HTTPException):
                role = None
        if role:
            try:
                await interaction.user.add_roles(role)
            except (discord.Forbidden, discord.HTTPException):
                pass

        info["weekly_coins_spent"] = info.get("weekly_coins_spent", 0) + price
        shop_log = load_shop_log(guild_id)
        week_key = datetime.now(JST).strftime("%Y-W%W")
        shop_log.setdefault(week_key, {})
        shop_log[week_key]["royal_pass"] = shop_log[week_key].get("royal_pass", 0) + 1
        save_shop_log(guild_id, shop_log)
        save_data(guild_id, data)

        embed = discord.Embed(
            title="👑 ROYAL PASS 購入完了！",
            description=(
                f"{interaction.user.mention} がROYAL PASSを購入しました！\n\n"
                f"✨ XP獲得量 **+20%**（7日間）\n"
                f"🎁 デイリー報酬 **+50%**（7日間）\n"
                f"👑 特別ロール **{ROYAL_PASS_ROLE}** 付与！"
            ),
            color=discord.Color.from_rgb(255, 215, 0)
        )
        embed.set_footer(text="有効期間：7日間")
        await interaction.response.send_message(embed=embed)

        ch_id = get_level_channel_id(guild_id)
        notify_ch = interaction.guild.get_channel(ch_id) if ch_id else None
        if notify_ch and notify_ch != interaction.channel:
            try:
                await notify_ch.send(f"👑 **{interaction.user.display_name}** が **ROYAL PASS** を購入しました！")
            except (discord.Forbidden, discord.HTTPException):
                pass

    # =========================
    # /shopstats
    # =========================
    @discord.app_commands.command(name="shopstats", description="全期間の人気アイテムTOP5を表示")
    async def shopstats(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        shop_log = load_shop_log(guild_id)

        all_time = {}
        for week_data in shop_log.values():
            for item_id, count in week_data.items():
                all_time[item_id] = all_time.get(item_id, 0) + count

        item_ranking = sorted(all_time.items(), key=lambda x: x[1], reverse=True)
        medals = ["🥇", "🥈", "🥉"]
        item_lines = []
        for i, (item_id, count) in enumerate(item_ranking[:5], start=1):
            item_name = SHOP_ITEMS.get(item_id, {}).get("name", item_id)
            medal = medals[i - 1] if i <= 3 else f"`{i}.`"
            item_lines.append(f"{medal} **{item_name}** … {count}回購入")

        item_text = "\n".join(item_lines) if item_lines else "まだデータがありません。"
        embed = discord.Embed(
            title="🔥 人気アイテム TOP5（全期間）",
            description=item_text,
            color=discord.Color.purple()
        )
        await interaction.response.send_message(embed=embed)

    # =========================
    # /servercoinsranking
    # =========================
    @discord.app_commands.command(name="servercoinsranking", description="今週の全サーバーコイン獲得・消費ランキングを表示")
    async def servercoinsranking(self, interaction: discord.Interaction):
        await interaction.response.defer()

        medals = ["🥇", "🥈", "🥉"]
        server_earned = []
        server_spent  = []

        for guild in self.bot.guilds:
            data = load_data(guild.id)
            total_earned = sum(
                info.get("weekly_coins_earned", 0)
                for uid, info in data.items()
                if uid != LAST_DECAY_KEY and isinstance(info, dict)
            )
            total_spent = sum(
                info.get("weekly_coins_spent", 0)
                for uid, info in data.items()
                if uid != LAST_DECAY_KEY and isinstance(info, dict)
            )
            t_disp = title_display(resolve_display_title(guild.id))
            display_name = f"{guild.name}{t_disp}"
            if total_earned > 0:
                server_earned.append((display_name, total_earned))
            if total_spent > 0:
                server_spent.append((display_name, total_spent))

        server_earned.sort(key=lambda x: x[1], reverse=True)
        server_spent.sort(key=lambda x: x[1], reverse=True)

        def build_server_lines(ranking):
            lines = []
            for i, (name, coins) in enumerate(ranking[:10], start=1):
                medal = medals[i - 1] if i <= 3 else f"`{i}.`"
                lines.append(f"{medal} **{name}** … {coins:,}コイン")
            return "\n".join(lines) if lines else "まだデータがありません。"

        embed = discord.Embed(title="🌐 全サーバー 週間コインランキング", color=discord.Color.gold())
        embed.add_field(name="📈 獲得数 TOP10", value=build_server_lines(server_earned), inline=False)
        embed.add_field(name="🛍️ 消費数 TOP10", value=build_server_lines(server_spent),  inline=False)
        embed.set_footer(text="集計期間：今週（月曜リセット）")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(ShopCog(bot))
