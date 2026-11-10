"""Constants for the 微信流量主 (WeChat Publisher) integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "wechat_publisher"
NAME: Final = "微信流量主"
VERSION: Final = "1.0.0"

# ---------------------------------------------------------------------------
# 配置项
# ---------------------------------------------------------------------------
CONF_ACCOUNT_NAME: Final = "account_name"
CONF_ACCOUNT_TYPE: Final = "account_type"
CONF_APPID: Final = "appid"
CONF_APPSECRET: Final = "appsecret"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LOOKBACK_DAYS: Final = "lookback_days"
CONF_FETCH_SETTLEMENT: Final = "fetch_settlement"

ACCOUNT_TYPE_MINIPROGRAM: Final = "miniprogram"
ACCOUNT_TYPE_OFFICIAL: Final = "official"
ACCOUNT_TYPES: Final = [ACCOUNT_TYPE_MINIPROGRAM, ACCOUNT_TYPE_OFFICIAL]

# ---------------------------------------------------------------------------
# 微信开放接口
# ---------------------------------------------------------------------------
API_HOST: Final = "https://api.weixin.qq.com"
URL_TOKEN: Final = f"{API_HOST}/cgi-bin/token"
URL_STAT: Final = f"{API_HOST}/publisher/stat"

ACTION_ADPOS_GENERAL: Final = "publisher_adpos_general"
ACTION_ADPOS_GENERAL_LEGACY: Final = "publisher_biz_adpos_general"
ACTION_SETTLEMENT: Final = "publisher_settlement"

#: 官方文档规定每页最大 90 条
PAGE_SIZE: Final = 90
#: 单次请求最大时间跨度（天）
MAX_SPAN_DAYS: Final = 90
#: 分页保护，避免异常返回导致死循环
MAX_PAGES: Final = 25
#: 请求超时（秒）
REQUEST_TIMEOUT: Final = 25
#: access_token 提前失效的缓冲时间（秒）
TOKEN_EXPIRY_MARGIN: Final = 300

#: 需要刷新 token 后重试的错误码
TOKEN_ERROR_CODES: Final = {40001, 42001}

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------
DEFAULT_SCAN_INTERVAL_HOURS: Final = 1
DEFAULT_SCAN_INTERVAL: Final = timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS)
MIN_SCAN_INTERVAL: Final = timedelta(minutes=10)
MAX_SCAN_INTERVAL: Final = timedelta(hours=24)
DEFAULT_LOOKBACK_DAYS: Final = 30
MIN_LOOKBACK_DAYS: Final = 7
MAX_LOOKBACK_DAYS: Final = 90

STORAGE_VERSION: Final = 1
STORAGE_KEY_TOKENS: Final = f"{DOMAIN}.tokens"

# ---------------------------------------------------------------------------
# 广告位类型名称 -> 中文名
# ---------------------------------------------------------------------------
SLOT_NAMES: Final[dict[str, str]] = {
    "SLOT_ID_BIZ_BOTTOM": "公众号底部广告",
    "SLOT_ID_BIZ_MID_CONTEXT": "公众号文中广告",
    "SLOT_ID_BIZ_VIDEO_END": "公众号视频后贴",
    "SLOT_ID_BIZ_SPONSOR": "公众号互选广告",
    "SLOT_ID_BIZ_CPS": "公众号返佣商品",
    "SLOT_ID_WEAPP_BANNER": "小程序 Banner",
    "SLOT_ID_WEAPP_REWARD_VIDEO": "小程序激励视频",
    "SLOT_ID_WEAPP_INTERSTITIAL": "小程序插屏广告",
    "SLOT_ID_WEAPP_VIDEO_FEEDS": "小程序视频广告",
    "SLOT_ID_WEAPP_VIDEO_BEGIN": "小程序视频前贴",
    "SLOT_ID_WEAPP_BOX": "小程序格子广告",
    "SLOT_ID_WEAPP_TEMPLATE": "小程序原生模板",
    "SLOT_ID_WEAPP_COVER": "小程序封面广告",
}
