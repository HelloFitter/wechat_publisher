"""微信流量主传感器平台。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import AccountSnapshot, WeChatPublisherCoordinator
from .entity import WeChatPublisherEntity

_LOGGER = logging.getLogger(__name__)

CURRENCY = "CNY"

PARALLEL_UPDATES = 1


# ---------------------------------------------------------------------------
# 取值函数
# ---------------------------------------------------------------------------
def _yesterday_income(snapshot: AccountSnapshot) -> float | None:
    stat = snapshot.yesterday
    return stat.income if stat else None


def _yesterday_exposure(snapshot: AccountSnapshot) -> int | None:
    stat = snapshot.yesterday
    return stat.exposure if stat else None


def _yesterday_clicks(snapshot: AccountSnapshot) -> int | None:
    stat = snapshot.yesterday
    return stat.clicks if stat else None


def _yesterday_click_rate(snapshot: AccountSnapshot) -> float | None:
    stat = snapshot.yesterday
    return stat.ctr if stat else None


def _yesterday_ecpm(snapshot: AccountSnapshot) -> float | None:
    stat = snapshot.yesterday
    return stat.ecpm if stat else None


def _yesterday_req(snapshot: AccountSnapshot) -> int | None:
    stat = snapshot.yesterday
    return stat.req if stat else None


def _income_7d(snapshot: AccountSnapshot) -> float:
    return snapshot.window(7).income


def _income_30d(snapshot: AccountSnapshot) -> float:
    return snapshot.window(30).income


def _income_month(snapshot: AccountSnapshot) -> float:
    days = snapshot.report_date.day
    return snapshot.window(days).income


def _settlement_value(key: str) -> Callable[[AccountSnapshot], float | None]:
    """结算相关取值。"""

    def _getter(snapshot: AccountSnapshot) -> float | None:
        settlement = snapshot.settlement
        if not settlement:
            return None
        try:
            return round(float(settlement.get(key) or 0) / 100, 2)
        except (TypeError, ValueError):
            return None

    return _getter


def _income_pending(snapshot: AccountSnapshot) -> float | None:
    settlement = snapshot.settlement
    if not settlement:
        return None
    try:
        total = float(settlement.get("revenue_all") or 0)
        settled = float(settlement.get("settled_revenue_all") or 0)
    except (TypeError, ValueError):
        return None
    return round((total - settled) / 100, 2)


def _data_date(snapshot: AccountSnapshot) -> str | None:
    return snapshot.report_date.isoformat()


# ---------------------------------------------------------------------------
# 附加属性
# ---------------------------------------------------------------------------
def _yesterday_attrs(snapshot: AccountSnapshot) -> dict[str, Any]:
    stat = snapshot.yesterday
    attrs: dict[str, Any] = {
        "date": snapshot.report_date.isoformat(),
        "data_date": snapshot.data_date.isoformat() if snapshot.data_date else None,
        "account_type": snapshot.account_type,
        "api_action": snapshot.action,
        "income_7d": snapshot.window(7).income,
        "income_30d": snapshot.window(30).income,
        "history": snapshot.history(),
        "slots": snapshot.slot_breakdown(),
    }
    if stat:
        attrs.update(
            {
                "exposure": stat.exposure,
                "clicks": stat.clicks,
                "click_rate": stat.ctr,
                "ecpm": stat.ecpm,
                "req_succ_count": stat.req,
            }
        )
    change = snapshot.wow_change
    if change is not None:
        attrs["change_pct"] = change
    return attrs


def _window_attrs(snapshot: AccountSnapshot, days: int) -> dict[str, Any]:
    window = snapshot.window(days)
    return {
        "end_date": snapshot.report_date.isoformat(),
        "days": days,
        **window.as_dict(),
    }


def _settlement_attrs(snapshot: AccountSnapshot) -> dict[str, Any]:
    settlement = snapshot.settlement or {}
    rows = settlement.get("settlement_list") or []
    recent = [
        {
            "month": item.get("month"),
            "zone": item.get("zone"),
            "status": item.get("sett_status"),
            "settled_revenue": round(float(item.get("settled_revenue") or 0) / 100, 2),
            "sett_no": item.get("sett_no"),
        }
        for item in rows[:12]
        if isinstance(item, dict)
    ]
    return {
        "body": settlement.get("body"),
        "revenue_all": _settlement_value("revenue_all")(snapshot),
        "settled_revenue_all": _settlement_value("settled_revenue_all")(snapshot),
        "penalty_all": _settlement_value("penalty_all")(snapshot),
        "settlements": recent,
    }


def _data_date_attrs(snapshot: AccountSnapshot) -> dict[str, Any]:
    latest_income = snapshot.yesterday.income if snapshot.yesterday else None
    return {
        "data_date": snapshot.data_date.isoformat() if snapshot.data_date else None,
        "report_date": snapshot.report_date.isoformat(),
        "latest_income": latest_income,
        "days_cached": len(snapshot.daily),
        "fetched_at": snapshot.fetched_at.isoformat() if snapshot.fetched_at else None,
    }


# ---------------------------------------------------------------------------
# 实体描述
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True)
class WeChatSensorEntityDescription(SensorEntityDescription):
    """带取值函数的实体描述。"""

    value_fn: Callable[[AccountSnapshot], Any] = lambda _: None
    attrs_fn: Callable[[AccountSnapshot], dict[str, Any]] | None = None


SENSORS: tuple[WeChatSensorEntityDescription, ...] = (
    WeChatSensorEntityDescription(
        key="yesterday_income",
        translation_key="yesterday_income",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=2,
        icon="mdi:cash-multiple",
        value_fn=_yesterday_income,
        attrs_fn=_yesterday_attrs,
    ),
    WeChatSensorEntityDescription(
        key="yesterday_exposure",
        translation_key="yesterday_exposure",
        icon="mdi:eye-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_yesterday_exposure,
    ),
    WeChatSensorEntityDescription(
        key="yesterday_clicks",
        translation_key="yesterday_clicks",
        icon="mdi:cursor-default-click-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_yesterday_clicks,
    ),
    WeChatSensorEntityDescription(
        key="yesterday_click_rate",
        translation_key="yesterday_click_rate",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:percent-outline",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_yesterday_click_rate,
    ),
    WeChatSensorEntityDescription(
        key="yesterday_ecpm",
        translation_key="yesterday_ecpm",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        icon="mdi:chart-line-variant",
        suggested_display_precision=2,
        value_fn=_yesterday_ecpm,
    ),
    WeChatSensorEntityDescription(
        key="yesterday_req",
        translation_key="yesterday_req",
        icon="mdi:download-network-outline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_yesterday_req,
    ),
    WeChatSensorEntityDescription(
        key="income_7d",
        translation_key="income_7d",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        icon="mdi:cash-clock",
        suggested_display_precision=2,
        value_fn=_income_7d,
        attrs_fn=lambda snap: _window_attrs(snap, 7),
    ),
    WeChatSensorEntityDescription(
        key="income_30d",
        translation_key="income_30d",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        icon="mdi:calendar-month-outline",
        suggested_display_precision=2,
        value_fn=_income_30d,
        attrs_fn=lambda snap: _window_attrs(snap, 30),
    ),
    WeChatSensorEntityDescription(
        key="income_month",
        translation_key="income_month",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        icon="mdi:calendar-today",
        suggested_display_precision=2,
        value_fn=_income_month,
        attrs_fn=lambda snap: _window_attrs(snap, snap.report_date.day),
    ),
    WeChatSensorEntityDescription(
        key="income_total",
        translation_key="income_total",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        icon="mdi:sack",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_settlement_value("revenue_all"),
        attrs_fn=_settlement_attrs,
    ),
    WeChatSensorEntityDescription(
        key="income_settled",
        translation_key="income_settled",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        icon="mdi:bank-check",
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_settlement_value("settled_revenue_all"),
        attrs_fn=_settlement_attrs,
    ),
    WeChatSensorEntityDescription(
        key="income_pending",
        translation_key="income_pending",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        icon="mdi:clock-alert-outline",
        suggested_display_precision=2,
        value_fn=_income_pending,
        attrs_fn=_settlement_attrs,
    ),
    WeChatSensorEntityDescription(
        key="data_date",
        translation_key="data_date",
        icon="mdi:calendar-sync-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_data_date,
        attrs_fn=_data_date_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """创建该账号的全部传感器。"""
    coordinator: WeChatPublisherCoordinator = entry.runtime_data.coordinator
    async_add_entities(
        WeChatPublisherSensor(coordinator, description) for description in SENSORS
    )


class WeChatPublisherSensor(WeChatPublisherEntity, SensorEntity):
    """微信流量主传感器。"""

    entity_description: WeChatSensorEntityDescription

    def __init__(
        self,
        coordinator: WeChatPublisherCoordinator,
        description: WeChatSensorEntityDescription,
    ) -> None:
        """初始化。"""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """返回当前值。"""
        try:
            return self.entity_description.value_fn(self.snapshot)
        except (KeyError, TypeError, ValueError):  # pragma: no cover - 防御
            _LOGGER.debug("计算 %s 时出错", self.entity_id, exc_info=True)
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """附加属性。"""
        if self.entity_description.attrs_fn is None:
            return None
        try:
            return self.entity_description.attrs_fn(self.snapshot)
        except (KeyError, TypeError, ValueError):  # pragma: no cover - 防御
            _LOGGER.debug("生成 %s 属性时出错", self.entity_id, exc_info=True)
            return None
