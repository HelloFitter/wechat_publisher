"""微信流量主的配置流程。"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    WeChatAuthError,
    WeChatConnectionError,
    WeChatError,
    WeChatIpNotAllowedError,
    WeChatNotPublisherError,
    WeChatPublisherApi,
    WeChatRateLimitError,
    async_get_token_store,
)
from .const import (
    ACCOUNT_TYPE_MINIPROGRAM,
    ACCOUNT_TYPE_OFFICIAL,
    CONF_ACCOUNT_NAME,
    CONF_ACCOUNT_TYPE,
    CONF_APPID,
    CONF_APPSECRET,
    CONF_FETCH_SETTLEMENT,
    CONF_LOOKBACK_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
    MAX_LOOKBACK_DAYS,
    MIN_LOOKBACK_DAYS,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT_NAME): selector.TextSelector(),
        vol.Required(CONF_ACCOUNT_TYPE, default=ACCOUNT_TYPE_OFFICIAL): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(
                        value=ACCOUNT_TYPE_OFFICIAL, label="公众号"
                    ),
                    selector.SelectOptionDict(
                        value=ACCOUNT_TYPE_MINIPROGRAM, label="小程序 / 小游戏"
                    ),
                ],
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
        vol.Required(CONF_APPID): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.TEXT, autocomplete="off"
            )
        ),
        vol.Required(CONF_APPSECRET): selector.TextSelector(
            selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD, autocomplete="off"
            )
        ),
    }
)

OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_HOURS
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.2,
                max=24,
                step=0.1,
                unit_of_measurement="小时",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_LOOKBACK_DAYS, default=DEFAULT_LOOKBACK_DAYS
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_LOOKBACK_DAYS,
                max=MAX_LOOKBACK_DAYS,
                step=1,
                unit_of_measurement="天",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_FETCH_SETTLEMENT, default=True
        ): selector.BooleanSelector(),
    }
)


class WeChatPublisherConfigFlow(ConfigFlow, domain=DOMAIN):
    """添加账号的配置流程。"""

    VERSION = 1

    def __init__(self) -> None:
        """初始化。"""
        self._reauth_entry: ConfigEntry | None = None
        self._detected_ip: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """手动添加一个公众号 / 小程序。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            user_input[CONF_APPID] = user_input[CONF_APPID].strip()
            user_input[CONF_APPSECRET] = user_input[CONF_APPSECRET].strip()
            user_input[CONF_ACCOUNT_NAME] = user_input[CONF_ACCOUNT_NAME].strip()

            errors = await self._async_try_connect(
                user_input[CONF_APPID], user_input[CONF_APPSECRET]
            )
            if not errors:
                await self.async_set_unique_id(user_input[CONF_APPID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_ACCOUNT_NAME], data=user_input
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, user_input or {}
            ),
            errors=errors,
            description_placeholders={
                "ip": self._detected_ip or "未知（请查看日志）"
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """凭据失效时的重新授权入口。"""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """让用户重新填写 AppSecret。"""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        if entry is None:  # pragma: no cover - 防御
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            appsecret = user_input[CONF_APPSECRET].strip()
            errors = await self._async_try_connect(entry.data[CONF_APPID], appsecret)
            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_APPSECRET: appsecret}
                )
                await self.async_set_unique_id(entry.data[CONF_APPID])
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_APPSECRET): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD,
                            autocomplete="off",
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "name": entry.title,
                "appid": entry.data[CONF_APPID],
                "ip": self._detected_ip or "未知（请查看日志）",
            },
        )

    async def _async_try_connect(self, appid: str, appsecret: str) -> dict[str, str]:
        """调用微信接口验证凭据，返回错误字典（空字典表示成功）。"""
        session = async_get_clientsession(self.hass)
        api = WeChatPublisherApi(session, appid, appsecret, async_get_token_store(self.hass))
        try:
            await api.async_validate()
        except WeChatIpNotAllowedError as err:
            self._detected_ip = err.ip
            _LOGGER.warning(
                "微信接口提示 IP 未加入白名单：%s（建议添加 %s）", err.message, err.ip
            )
            return {"base": "invalid_ip"}
        except WeChatNotPublisherError:
            return {"base": "not_publisher"}
        except WeChatAuthError:
            return {"base": "invalid_auth"}
        except WeChatRateLimitError:
            return {"base": "rate_limited"}
        except WeChatConnectionError:
            return {"base": "cannot_connect"}
        except WeChatError as err:
            _LOGGER.error("校验微信账号失败：%s", err)
            return {"base": "unknown"}
        return {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """返回选项流程。"""
        return WeChatPublisherOptionsFlow()


class WeChatPublisherOptionsFlow(OptionsFlow):
    """更新间隔与数据范围设置。"""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """显示/保存选项。"""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA,
                {
                    CONF_SCAN_INTERVAL: options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_HOURS
                    ),
                    CONF_LOOKBACK_DAYS: options.get(
                        CONF_LOOKBACK_DAYS, DEFAULT_LOOKBACK_DAYS
                    ),
                    CONF_FETCH_SETTLEMENT: options.get(CONF_FETCH_SETTLEMENT, True),
                },
            ),
        )
