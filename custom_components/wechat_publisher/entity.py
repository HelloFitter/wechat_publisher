"""微信流量主实体的公共基类。"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AccountSnapshot, WeChatPublisherCoordinator


class WeChatPublisherEntity(CoordinatorEntity[WeChatPublisherCoordinator]):
    """所有实体共享的设备信息与可用性逻辑。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WeChatPublisherCoordinator) -> None:
        """初始化。"""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.account_name,
            manufacturer="微信公众平台",
            model=f"流量主（{coordinator.account_type_label}）",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def snapshot(self) -> AccountSnapshot:
        """当前数据快照。"""
        return self.coordinator.data
