# XPブースト状態管理（サーバーごと・インメモリ）
# bot再起動でリセットされる（将来的にconfig永続化予定）

# 時間帯ブースト: { guild_id: multiplier }  1=無効
guild_time_boost: dict[int, float] = {}
# ボス討伐ブースト: { guild_id: multiplier }  1=無効
guild_boss_boost: dict[int, float] = {}

def get_boost(guild_id: int) -> dict:
    """2つのブーストを掛け合わせた最終倍率を返す"""
    time_m = guild_time_boost.get(guild_id, 1)
    boss_m = guild_boss_boost.get(guild_id, 1)
    total  = time_m * boss_m
    return {"multiplier": total, "active": total > 1}

def set_time_boost(guild_id: int, multiplier: float) -> None:
    guild_time_boost[guild_id] = multiplier

def set_boss_boost(guild_id: int, multiplier: float) -> None:
    guild_boss_boost[guild_id] = multiplier
