"""数据协调器：把微信原始数据聚合成实体可用的快照。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    WeChatAuthError,
    WeChatConnectionError,
    WeChatError,
    WeChatIpNotAllowedError,
    WeChatNotPublisherError,
    WeChatPublisherApi,
    WeChatRateLimitError,
)
from .const import (
    ACCOUNT_TYPE_MINIPROGRAM,
    ACCOUNT_TYPE_OFFICIAL,
    CONF_ACCOUNT_NAME,
    CONF_ACCOUNT_TYPE,
    CONF_FETCH_SETTLEMENT,
    CONF_LOOKBACK_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SLOT_NAMES,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SlotStat:
    """单个广告位在一天内的表现。"""

    income_cents: int = 0
    exposure: int = 0
    clicks: int = 0
    req: int = 0

    @property
    def key(self) -> str:
        """占位，便于调试。"""
        return f"{self.income_cents}/{self.exposure}/{self.clicks}"


@dataclass(slots=True)
class DailyStat:
    """一天的汇总数据。"""

    day: date
    income_cents: int = 0
    exposure: int = 0
    clicks: int = 0
    req: int = 0
    slots: dict[str, SlotStat] = field(default_factory=dict)

    @property
    def income(self) -> float:
        """收入（元）。"""
        return round(self.income_cents / 100, 2)

    @property
    def ecpm(self) -> float:
        """eCPM（元）。"""
        if self.exposure:
            return round(self.income_cents / self.exposure * 1000 / 100, 4)
        return 0.0

    @property
    def ctr(self) -> float:
        """点击率（%）。"""
        if self.exposure:
            return round(self.clicks / self.exposure * 100, 2)
        return 0.0

    def as_dict(self) -> dict[str, Any]:
        """导出为字典，用作实体属性。"""
        return {
            "date": self.day.isoformat(),
            "income": self.income,
            "exposure": self.exposure,
            "clicks": self.clicks,
            "click_rate": self.ctr,
            "ecpm": self.ecpm,
            "req_succ_count": self.req,
        }


@dataclass(slots=True)
class RangeStat:
    """一个区间的汇总。"""

    income_cents: int = 0
    exposure: int = 0
    clicks: int = 0
    req: int = 0
    days_with_data: int = 0

    @property
    def income(self) -> float:
        """收入（元）。"""
        return round(self.income_cents / 100, 2)

    @property
    def ctr(self) -> float:
        """点击率（%）。"""
        if self.exposure:
            return round(self.clicks / self.exposure * 100, 2)
        return 0.0

    @property
    def ecpm(self) -> float:
        """eCPM（元）。"""
        if self.exposure:
            return round(self.income_cents / self.exposure * 1000 / 100, 4)
        return 0.0

    def as_dict(self) -> dict[str, Any]:
        """导出为字典。"""
        return {
            "income": self.income,
            "exposure": self.exposure,
            "clicks": self.clicks,
            "click_rate": self.ctr,
            "ecpm": self.ecpm,
            "days_with_data": self.days_with_data,
        }


@dataclass(slots=True)
class AccountSnapshot:
    """一个账号的完整数据快照。"""

    report_date: date
    data_date: date | None
    daily: dict[str, DailyStat]
    settlement: dict[str, Any] | None
    action: str
    account_type: str
    fetched_at: datetime | None = None

    # -- 便捷访问 ---------------------------------------------------------
    @property
    def yesterday(self) -> DailyStat | None:
        """报表日（有数据的最新一天）的数据。"""
        return self.daily.get(self.report_date.isoformat())

    @property
    def previous_day(self) -> DailyStat | None:
        """报表日前一天的数据。"""
        prev = self.report_date - timedelta(days=1)
        return self.daily.get(prev.isoformat())

    def window(self, days: int) -> RangeStat:
        """取以报表日为终点、长度为 days 的区间汇总。"""
        return _summarize(self.daily, self.report_date, days)

    def slot_breakdown(self) -> dict[str, Any]:
        """报表日的广告位构成。"""
        yesterday = self.yesterday
        if not yesterday:
            return {}
        result: dict[str, Any] = {}
        for slot, stat in sorted(
            yesterday.slots.items(), key=lambda item: item[1].income_cents, reverse=True
        ):
            name = SLOT_NAMES.get(slot, slot)
            result[name] = {
                "income": round(stat.income_cents / 100, 2),
                "exposure": stat.exposure,
                "clicks": stat.clicks,
                "click_rate": round(stat.clicks / stat.exposure * 100, 2)
                if stat.exposure
                else 0.0,
                "ecpm": round(stat.income_cents / stat.exposure * 1000 / 100, 4)
                if stat.exposure
                else 0.0,
            }
        return result

    def history(self) -> dict[str, float]:
        """按日期升序的收入历史（元），可直接喂给 ApexCharts。"""
        return {
            key: round(stat.income_cents / 100, 2)
            for key, stat in sorted(self.daily.items())
        }

    @property
    def wow_change(self) -> float | None:
        """报表日相对前一天的收益变化（%）。"""
        yesterday = self.yesterday
        prev = self.previous_day
        if not yesterday or not prev or not prev.income_cents:
            return None
        return round(
            (yesterday.income_cents - prev.income_cents) / prev.income_cents * 100, 2
        )


def _summarize(daily: dict[str, DailyStat], end: date, days: int) -> RangeStat:
    """汇总 [end-days+1, end] 区间。"""
    total = RangeStat()
    for offset in range(days):
        day = end - timedelta(days=offset)
        stat = daily.get(day.isoformat())
        if stat is None:
            continue
        total.income_cents += stat.income_cents
        total.exposure += stat.exposure
        total.clicks += stat.clicks
        total.req += stat.req
        total.days_with_data += 1
    return total


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, DailyStat]:
    """把接口返回的行（日期 × 广告位）聚合成按天索引的字典。"""
    daily: dict[str, DailyStat] = {}
    for row in rows:
        raw_date = row.get("date")
        if not raw_date:
            continue
        try:
            day = date.fromisoformat(str(raw_date))
        except ValueError:
            continue

        slot = str(row.get("ad_slot") or row.get("slot_id") or "UNKNOWN")
        income = _as_int(row.get("income"))
        exposure = _as_int(row.get("exposure_count"))
        clicks = _as_int(row.get("click_count"))
        req = _as_int(row.get("req_succ_count"))

        stat = daily.get(day.isoformat())
        if stat is None:
            stat = daily[day.isoformat()] = DailyStat(day=day)
        stat.income_cents += income
        stat.exposure += exposure
        stat.clicks += clicks
        stat.req += req

        slot_stat = stat.slots.get(slot)
        if slot_stat is None:
            slot_stat = stat.slots[slot] = SlotStat()
        slot_stat.income_cents += income
        slot_stat.exposure += exposure
        slot_stat.clicks += clicks
        slot_stat.req += req
    return daily


def _as_int(value: Any) -> int:
    """宽松转 int。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


class WeChatPublisherCoordinator(DataUpdateCoordinator[AccountSnapshot]):
    """负责定时拉取并聚合单个账号的数据。"""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: WeChatPublisherApi,
    ) -> None:
        """初始化。"""
        options = {**entry.data, **entry.options}
        self.api = api
        self.account_name: str = options.get(CONF_ACCOUNT_NAME) or entry.title
        self.account_type: str = options.get(
            CONF_ACCOUNT_TYPE, ACCOUNT_TYPE_MINIPROGRAM
        )
        self.lookback_days: int = int(
            options.get(CONF_LOOKBACK_DAYS, DEFAULT_LOOKBACK_DAYS)
        )
        self.fetch_settlement: bool = bool(options.get(CONF_FETCH_SETTLEMENT, True))

        scan_interval = options.get(CONF_SCAN_INTERVAL)
        update_interval = (
            timedelta(hours=float(scan_interval))
            if scan_interval
            else DEFAULT_SCAN_INTERVAL
        )

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.account_name}",
            update_interval=update_interval,
            config_entry=entry,
        )

    @property
    def account_type_label(self) -> str:
        """账号类型中文名。"""
        return (
            "公众号"
            if self.account_type == ACCOUNT_TYPE_OFFICIAL
            else "小程序"
        )

    async def _async_update_data(self) -> AccountSnapshot:
        """拉取全量数据。"""
        today = dt_util.now().date()
        start = today - timedelta(days=self.lookback_days)

        try:
            rows, action = await self.api.async_get_daily_stats(start, today)
        except WeChatIpNotAllowedError as err:
            raise UpdateFailed(
                "服务器 IP 未加入公众平台后台的 IP 白名单"
                f"（{err.ip or '未知 IP'}），请到「设置与开发 → 基本配置 → IP 白名单」中添加后重试"
            ) from err
        except WeChatAuthError as err:
            raise ConfigEntryAuthFailed(
                f"微信鉴权失败（{err.code}）：{err.message}"
            ) from err
        except WeChatNotPublisherError as err:
            raise UpdateFailed(
                f"该账号尚未开通流量主，无法获取数据：{err.message}"
            ) from err
        except WeChatRateLimitError as err:
            raise UpdateFailed(f"接口调用频率超限，稍后自动重试：{err.message}") from err
        except WeChatConnectionError as err:
            raise UpdateFailed(f"网络异常：{err.message}") from err
        except WeChatError as err:
            raise UpdateFailed(f"微信接口返回错误：{err.message}") from err

        daily = _aggregate(rows)
        data_date = max((stat.day for stat in daily.values()), default=None)
        report_date = data_date if data_date else today - timedelta(days=1)
        if report_date > today:  # 理论上不会出现，做一层保护
            report_date = today

        settlement: dict[str, Any] | None = None
        if self.fetch_settlement:
            try:
                settlement = await self.api.async_get_settlement(
                    today - timedelta(days=90), today
                )
            except WeChatIpNotAllowedError as err:
                raise UpdateFailed(
                    f"服务器 IP 未加入白名单（{err.ip or '未知 IP'}）"
                ) from err
            except WeChatAuthError as err:
                raise ConfigEntryAuthFailed(
                    f"微信鉴权失败（{err.code}）：{err.message}"
                ) from err
            except WeChatError as err:
                _LOGGER.debug("结算数据获取失败，忽略：%s", err)

        return AccountSnapshot(
            report_date=report_date,
            data_date=data_date,
            daily=daily,
            settlement=settlement,
            action=action,
            account_type=self.account_type,
            fetched_at=dt_util.utcnow(),
        )
