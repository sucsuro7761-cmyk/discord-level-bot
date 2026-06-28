import discord
from discord.ext import commands

from utils.config import (
    add_earned_title,
    get_active_title,
    get_earned_titles,
    resolve_display_title,
    set_active_title,
)
from utils.constants import TITLE_DEFINITIONS, title_display, weekly_roles


def _can_manage_titles(interaction: discord.Interaction) -> bool:
    """サーバー管理者 または 週間TOP3ロール所持者のみ操作可"""
    if interaction.user.guild_permissions.manage_guild:
        return True
    weekly_role_names = set(weekly_roles.values())
    user_role_names = {r.name for r in interaction.user.roles}
    return bool(user_role_names & weekly_role_names)


class TitlesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # =========================
    # /titles（獲得済み一覧）
    # =========================
    @discord.app_commands.command(name="titles", description="このサーバーが獲得した称号一覧を表示します")
    async def titles(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        earned   = get_earned_titles(guild_id)
        active   = get_active_title(guild_id)

        if not earned:
            await interaction.response.send_message(
                "このサーバーはまだ称号を獲得していません。\nボスを倒したり週間ランキングで上位を目指してみよう！",
                ephemeral=True
            )
            return

        # ランダムモード時の今日の実際の称号
        today_title = resolve_display_title(guild_id) if active == "random" else active

        lines = []
        for tid in earned:
            defn = TITLE_DEFINITIONS.get(tid)
            if not defn:
                continue
            if active == "random":
                star = "🎲 **（ランダム中）**" if tid == today_title else ""
            else:
                star = "✅ **（表示中）**" if tid == active else ""
            lines.append(f"{defn['name']} {star}\n　└ {defn['description']}")

        embed = discord.Embed(
            title=f"🏅 {interaction.guild.name} の称号コレクション",
            description="\n\n".join(lines),
            color=discord.Color.gold()
        )
        if active == "random":
            today_defn = TITLE_DEFINITIONS.get(today_title, {})
            embed.set_footer(text=f"🎲 ランダムモード中 — 今日は「{today_defn.get('name', '?')}」を表示中")
        elif active:
            embed.set_footer(text=f"現在表示中の称号：{title_display(active)}")
        else:
            embed.set_footer(text="称号未設定 — /settitle で設定できます")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # =========================
    # /settitle（称号を選んで設定）
    # =========================
    @discord.app_commands.command(name="settitle", description="サーバーに表示する称号を設定します（管理者・週間TOP3のみ）")
    @discord.app_commands.describe(title_id="設定する称号ID（none で解除）")
    async def settitle(self, interaction: discord.Interaction, title_id: str):
        if not _can_manage_titles(interaction):
            await interaction.response.send_message(
                "⛔ この操作はサーバー管理者または週間TOP3ロールを持つ方のみ使用できます。",
                ephemeral=True
            )
            return

        guild_id = interaction.guild.id

        # 称号解除
        if title_id.lower() in ("none", "なし", "解除"):
            set_active_title(guild_id, None)
            await interaction.response.send_message("✅ 称号を解除しました。", ephemeral=True)
            return

        # ランダムモード
        if title_id.lower() == "random":
            earned = get_earned_titles(guild_id)
            if not earned:
                await interaction.response.send_message(
                    "❌ まだ称号を獲得していないためランダムモードは使用できません。",
                    ephemeral=True
                )
                return
            set_active_title(guild_id, "random")
            await interaction.response.send_message(
                "🎲 **ランダムモード** に設定しました！\n獲得済み称号の中から1日1回ランダムで表示が切り替わります。",
                ephemeral=True
            )
            return

        earned = get_earned_titles(guild_id)
        if title_id not in earned:
            earned_names = "\n".join(
                f"`{tid}` — {TITLE_DEFINITIONS[tid]['name']}"
                for tid in earned if tid in TITLE_DEFINITIONS
            ) or "（なし）"
            await interaction.response.send_message(
                f"❌ その称号はまだ獲得していません。\n\n**獲得済み称号**\n{earned_names}",
                ephemeral=True
            )
            return

        defn = TITLE_DEFINITIONS.get(title_id)
        if not defn:
            await interaction.response.send_message("❌ 不明な称号IDです。", ephemeral=True)
            return

        set_active_title(guild_id, title_id)
        await interaction.response.send_message(
            f"✅ 称号を **{defn['name']}** に設定しました！\nサーバー対抗ランキングに表示されます。",
            ephemeral=True
        )

    @settitle.autocomplete("title_id")
    async def settitle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[discord.app_commands.Choice[str]]:
        earned = get_earned_titles(interaction.guild.id)
        choices = [
            discord.app_commands.Choice(
                name=TITLE_DEFINITIONS[tid]["name"],
                value=tid
            )
            for tid in earned
            if tid in TITLE_DEFINITIONS and current.lower() in tid.lower()
        ]
        if "random".startswith(current.lower()) or "ランダム".startswith(current):
            choices.append(discord.app_commands.Choice(name="🎲 ランダム（1日1回自動切替）", value="random"))
        choices.append(discord.app_commands.Choice(name="（称号を解除する）", value="none"))
        return choices[:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(TitlesCog(bot))
