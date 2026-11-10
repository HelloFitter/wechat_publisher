"""诊断信息导出（自动隐藏 AppSecret）。"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_APPSECRET, DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """返回诊断信息。"""
    coordinator = entry.runtime_data.coordinator
    snapshot = coordinator.data

    data = dict(entry.data)
    if CONF_APPSECRET in data:
        data[CONF_APPSECRET] = "**REDACTED**"

    return {
        "domain": DOMAIN,
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": data,
            "options": dict(entry.options),
        },
        "account": {
            "name": coordinator.account_name,
            "type": coordinator.account_type,
            "lookback_days": coordinator.lookback_days,
            "fetch_settlement": coordinator.fetch_settlement,
            "update_interval_seconds": coordinator.update_interval.total_seconds()
            if coordinator.update_interval
            else None,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": str(coordinator.last_exception)
            if coordinator.last_exception
            else None,
        },
        "data": {
            "report_date": snapshot.report_date.isoformat(),
            "data_date": snapshot.data_date.isoformat() if snapshot.data_date else None,
            "api_action": snapshot.action,
            "days_cached": len(snapshot.daily),
            "history": snapshot.history(),
            "slots": snapshot.slot_breakdown(),
            "has_settlement": snapshot.settlement is not None,
        },
    }
