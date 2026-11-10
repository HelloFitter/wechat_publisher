"""微信流量主（WeChat Publisher）Home Assistant 集成。

把公众号 / 小程序的流量主收益数据接入 Home Assistant，
提供昨日收入、曝光点击、区间汇总、结算信息等传感器。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WeChatPublisherApi, async_get_token_store
from .const import DOMAIN
from .coordinator import AccountSnapshot, WeChatPublisherCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

SERVICE_REFRESH = "refresh"
SERVICE_GET_REPORT = "get_report"

REFRESH_SCHEMA = vol.Schema({vol.Optional("entry_id"): cv.string})

REPORT_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): cv.string,
        vol.Optional("days", default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=90)
        ),
    }
)


@dataclass
class WeChatPublisherRuntimeData:
    """配置条目运行时数据。"""

    api: WeChatPublisherApi
    coordinator: WeChatPublisherCoordinator


type WeChatPublisherConfigEntry = ConfigEntry[WeChatPublisherRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: WeChatPublisherConfigEntry) -> bool:
    """建立一个账号。"""
    api = WeChatPublisherApi(
        async_get_clientsession(hass),
        entry.data["appid"],
        entry.data["appsecret"],
        async_get_token_store(hass),
    )
    coordinator = WeChatPublisherCoordinator(hass, entry, api)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = WeChatPublisherRuntimeData(api=api, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_setup_services(hass)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: WeChatPublisherConfigEntry
) -> bool:
    """卸载一个账号。"""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: WeChatPublisherConfigEntry
) -> None:
    """选项变化后重载。"""
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------
def _async_setup_services(hass: HomeAssistant) -> None:
    """注册服务（只注册一次）。"""
    store: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    if store.get("services_registered"):
        return
    store["services_registered"] = True

    async def _async_handle_refresh(call: ServiceCall) -> None:
        for entry in _resolve_entries(hass, call.data.get("entry_id")):
            await entry.runtime_data.coordinator.async_refresh()

    async def _async_handle_get_report(call: ServiceCall) -> ServiceResponse:
        days: int = call.data["days"]
        entries = _resolve_entries(hass, call.data.get("entry_id"))
        if not entries:
            raise ServiceValidationError("还没有配置任何微信流量主账号")

        accounts: list[dict[str, Any]] = []
        for entry in entries:
            coordinator = entry.runtime_data.coordinator
            if entry.entry_id == call.data.get("entry_id"):
                # 单个账号时强制刷新，保证拿到最新数据
                await coordinator.async_refresh()
            accounts.append(_build_report(coordinator, days))

        total = round(sum(item["income"] for item in accounts), 2)
        report_date = accounts[0]["report_date"] if accounts else None
        return {
            "report_date": report_date,
            "total_income": total,
            "accounts": accounts,
            "text": _render_text(accounts, total, report_date, days),
        }

    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _async_handle_refresh, schema=REFRESH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_REPORT,
        _async_handle_get_report,
        schema=REPORT_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _resolve_entries(
    hass: HomeAssistant, entry_id: Any
) -> list[WeChatPublisherConfigEntry]:
    """按 entry_id 或全部返回已成功加载的配置条目。"""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if isinstance(getattr(entry, "runtime_data", None), WeChatPublisherRuntimeData)
    ]
    if entry_id:
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(f"找不到配置条目 {entry_id}")
    return entries


def _build_report(coordinator: WeChatPublisherCoordinator, days: int) -> dict[str, Any]:
    """组装单个账号的报告数据。"""
    snapshot: AccountSnapshot = coordinator.data
    window = snapshot.window(days)
    yesterday = snapshot.yesterday
    return {
        "entry_id": coordinator.config_entry.entry_id,
        "name": coordinator.account_name,
        "account_type": coordinator.account_type_label,
        "report_date": snapshot.report_date.isoformat(),
        "income": window.income,
        "exposure": window.exposure,
        "clicks": window.clicks,
        "click_rate": window.ctr,
        "ecpm": window.ecpm,
        "yesterday_income": yesterday.income if yesterday else 0.0,
        "income_total": _settlement_yuan(snapshot, "revenue_all"),
        "slots": snapshot.slot_breakdown(),
    }


def _settlement_yuan(snapshot: AccountSnapshot, key: str) -> float | None:
    """结算字段转元。"""
    settlement = snapshot.settlement
    if not settlement:
        return None
    try:
        return round(float(settlement.get(key) or 0) / 100, 2)
    except (TypeError, ValueError):
        return None


def _render_text(
    accounts: list[dict[str, Any]],
    total: float,
    report_date: str | None,
    days: int,
) -> str:
    """生成适合推送的纯文本报告。"""
    title = "微信流量主日报" if days == 1 else f"微信流量主近 {days} 天报表"
    lines = [f"📊 {title} · {report_date}"]
    for item in accounts:
        if days == 1:
            lines.append(
                f"• {item['name']}：¥{item['income']:.2f}"
                f"（曝光 {item['exposure']:,}｜点击 {item['clicks']:,}"
                f"｜CTR {item['click_rate']:.2f}%）"
            )
        else:
            lines.append(f"• {item['name']}：¥{item['income']:.2f}")
    lines.append(f"合计：¥{total:.2f}")
    return "\n".join(lines)
