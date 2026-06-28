import os
import random
import pytz
from datetime import datetime

# =========================
# 基本定数
# =========================
DECAY_PERCENT  = 0.05
LAST_DECAY_KEY = "last_decay"
DATA_DIR       = "/data"
JST            = pytz.timezone("Asia/Tokyo")

# bot管理者ID（環境変数 BOT_ADMIN_IDS にカンマ区切りで設定）
_raw_admin_ids = os.environ.get("BOT_ADMIN_IDS", "1118472855865266246")
BOT_ADMIN_IDS  = set(int(i.strip()) for i in _raw_admin_ids.split(",") if i.strip().isdigit())

def is_bot_admin(user_id: int) -> bool:
    return user_id in BOT_ADMIN_IDS

# =========================
# コイン / ショップ
# =========================
COIN_DAILY_CAP = 1500

SHOP_ITEMS = {
    "xp_small": {
        "name": "XPブースト（小）",
        "price": 1500,
        "description": "XP獲得量が1.5倍になります（10分）",
        "buff_type": "xp_multiplier",
        "value": 1.5,
        "duration": 10 * 60,
    },
    "xp_medium": {
        "name": "XPブースト（中）",
        "price": 2000,
        "description": "XP獲得量が2倍になります（10分）",
        "buff_type": "xp_multiplier",
        "value": 2.0,
        "duration": 10 * 60,
    },
    "daily_boost": {
        "name": "デイリー強化",
        "price": 1000,
        "description": "デイリー報酬が1.5倍になります（24時間）",
        "buff_type": "daily_multiplier",
        "value": 1.5,
        "duration": 24 * 60 * 60,
    },
    "attack_up": {
        "name": "攻撃力アップ",
        "price": 1000,
        "description": "ボスへのダメージが1.2倍になります（60分）",
        "buff_type": "damage_multiplier",
        "value": 1.2,
        "duration": 60 * 60,
    },
    "crit_up": {
        "name": "クリティカル強化",
        "price": 5000,
        "description": "クリティカル発生率がアップ！獲得XPに超高倍率ダメージ（15分）",
        "buff_type": "crit_bonus",
        "value": True,
        "duration": 15 * 60,
    },
    "boss_slayer": {
        "name": "ボス特効",
        "price": 2000,
        "description": "ボスへのダメージが1.3倍になります（15分）",
        "buff_type": "boss_damage_multiplier",
        "value": 1.3,
        "duration": 15 * 60,
    },
    "decay_guard": {
        "name": "🛡️ XP減衰ガード",
        "price": 2500,
        "description": "購入した日のXP自然減衰を無効化します（1日限定）",
        "buff_type": "decay_guard",
        "value": True,
        "duration": 24 * 60 * 60,
    },
    "rankdown_shield": {
        "name": "🔰 ランクダウン防止シールド",
        "price": 5000,
        "description": "次の1回のランクダウンを無効化します（使い切り）",
        "buff_type": "rankdown_shield",
        "value": True,
        "duration": None,
    },
    "royal_pass": {
        "name": "👑 ROYAL PASS",
        "price": 50000,
        "description": "XP+20%・デイリー報酬+50%・特別ロール付与（7日間）",
        "buff_type": "royal_pass",
        "value": True,
        "duration": 7 * 24 * 60 * 60,
    },
    "mystery_box": {
        "name": "🎁 ミステリーボックス",
        "price": 3000,
        "description": "開けると何かが飛び出す！ランダム報酬ボックス（専用コマンド /openbox で使用）",
        "buff_type": None,
        "value": None,
        "duration": None,
    },
    "investment_pack": {
        "name": "📈 投資パック",
        "price": 3000,
        "description": "500〜50,000コインを投資！24時間後に /claiminvest で回収（専用コマンド /invest で購入）",
        "buff_type": None,
        "value": None,
        "duration": None,
    },
}

# =========================
# クリティカルシステム
# =========================
CRIT_TABLE_TEXT = [
    ("超+CT", 0.001,  0.0005,  50, "💥"),
    ("超CT",  0.005,  0.0025,  25, "⚡"),
    ("CT",    0.03,   0.015,   10, "🔥"),
    ("ミニCT",0.05,   0.025,    5, "✨"),
]

CRIT_TABLE_VC = [
    ("超+CT", 0.0005,  0.00025,  50, "💥"),
    ("超CT",  0.0025,  0.00125,  25, "⚡"),
    ("CT",    0.015,   0.0075,   10, "🔥"),
    ("ミニCT",0.025,   0.0125,    5, "✨"),
]

CRIT_TABLE = CRIT_TABLE_TEXT  # 後方互換用

def calc_crit(base_xp, has_crit_buff, is_vc=False):
    """クリティカル判定を行い (最終XP, クリット名orNone, 倍率) を返す"""
    table = CRIT_TABLE_VC if is_vc else CRIT_TABLE_TEXT
    r = random.random()
    cumulative = 0.0
    for name, prob_buff, prob_normal, multiplier, emoji in table:
        prob = prob_buff if has_crit_buff else prob_normal
        cumulative += prob
        if r < cumulative:
            return int(base_xp * multiplier), f"{emoji} {name}！", multiplier
    return base_xp, None, 1

# =========================
# ボスシステム
# =========================
BOSS_BASE_HP  = 30000
BOSS_HP_SCALE = 1.2  # 後方互換用（calc_boss_hp を使うこと）

def calc_boss_hp(cleared: int) -> int:
    """段階的スケーリングでボスHPを計算する。
    1〜5回目:  ×1.20（序盤はドラマチック感）
    6〜15回目: ×1.10（中盤は緩やか）
    16回目以降: ×1.05（長期でも詰まらない）
    """
    hp = BOSS_BASE_HP
    for i in range(cleared):
        if i < 5:
            hp = int(hp * 1.20)
        elif i < 15:
            hp = int(hp * 1.10)
        else:
            hp = int(hp * 1.05)
    return hp

EVENT_BOSS_DEFAULT_HP          = 150000  # 手動召喚時のデフォルト値
EVENT_BOSS_HP_MULTIPLIER       = 1.5    # 自動出現時: 週ボスHP × この倍率
EVENT_BOSS_CLEAR_ROLE          = "👑BOSS VIP"
EVENT_BOSS_CONSECUTIVE_CLEARS  = 5
EVENT_BOSS_BOOST_MULTIPLIER    = 3
EVENT_BOSS_BOOST_DAYS          = 7

GLOBAL_EVENT_BOSS_FILE = os.path.join(DATA_DIR, "global_event_boss.json")

def get_boss_clear_role_name(cleared: int) -> str:
    return f"⚔️ LV{cleared}ボス討伐者"

# =========================
# ランク定義
# =========================
rank_roles = [
    (1,   9,    "MEMBER Lite"),
    (10,  29,   "MEMBER"),
    (30,  49,   "CORE"),
    (50,  74,   "SELECT"),
    (75,  99,   "PREMIUM"),
    (100, 199,  "VIP Lite"),
    (200, 499,  "VIP"),
    (500, 9999, "Legend"),
]

permanent_roles = {3: "PHOTO+"}

weekly_roles = {
    1: "🥇週間王者",
    2: "🥈週間準王",
    3: "🥉週間三位",
}

# =========================
# 称号定義
# =========================
# trigger の種類:
#   "on_join"      : bot 導入時に即時付与
#   "boss_defeat"  : ボス討伐時にチェック（threshold = 必要討伐回数）
#   "weekly_reset" : 週間リセット時にチェック
TITLE_DEFINITIONS: dict[str, dict] = {
    "newcomer": {
        "name": "🌱 新参者",
        "description": "むれちゃんbotを導入した",
        "trigger": "on_join",
    },
    "weekly_champion": {
        "name": "👑 週間王者サーバー",
        "description": "サーバー対抗週間XPランキングで1位を獲得",
        "trigger": "weekly_reset",
    },
    "boss_hunter": {
        "name": "⚔️ ボスハンター",
        "description": "サーバーで初めてボスを討伐した",
        "trigger": "boss_defeat",
        "threshold": 1,
    },
    "boss_master": {
        "name": "💀 ボスマスター",
        "description": "ボスを通算10回討伐した",
        "trigger": "boss_defeat",
        "threshold": 10,
    },
    "rich_community": {
        "name": "💰 リッチコミュニティ",
        "description": "週間コイン獲得が合計50,000コイン以上",
        "trigger": "weekly_reset",
        "threshold": 50000,
    },
}

# 称号表示用（サーバー名の後ろに付ける）
def title_display(title_id: str | None) -> str:
    """アクティブ称号がある場合に「【称号名】」を返す"""
    if not title_id or title_id not in TITLE_DEFINITIONS:
        return ""
    return f"【{TITLE_DEFINITIONS[title_id]['name']}】"

# =========================
# デイリーミッション定義
# =========================
DAILY_MISSIONS = {
    0: {"label": "💬 メッセージを5回送る",       "type": "msg_count",    "goal": 5,   "reward": 100},
    1: {"label": "💬 メッセージを10回送る",      "type": "msg_count",    "goal": 10,  "reward": 200},
    2: {"label": "🎙️ VCに30分滞在する",         "type": "vc_minutes",   "goal": 30,  "reward": 200},
    3: {"label": "⚔️ ボスに300ダメージ与える",   "type": "boss_damage",  "goal": 300, "reward": 500},
    4: {"label": "🎁 チェストを3回開ける",       "type": "chest_count",  "goal": 3,   "reward": 300},
    5: {"label": "📈 投資パックを購入する",      "type": "invest_done",  "goal": 1,   "reward": 500},
    6: {"label": "⭐ XPを500獲得する",           "type": "xp_gained",    "goal": 500, "reward": 300},
}
