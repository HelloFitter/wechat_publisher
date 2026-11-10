"""微信流量主 OpenAPI 客户端。

封装了 access_token 的获取与缓存、广告汇总数据（含分页）以及结算数据接口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Mapping
from datetime import date
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    ACTION_ADPOS_GENERAL,
    ACTION_ADPOS_GENERAL_LEGACY,
    ACTION_SETTLEMENT,
    DOMAIN,
    MAX_PAGES,
    MAX_SPAN_DAYS,
    PAGE_SIZE,
    REQUEST_TIMEOUT,
    STORAGE_KEY_TOKENS,
    STORAGE_VERSION,
    TOKEN_ERROR_CODES,
    TOKEN_EXPIRY_MARGIN,
    URL_STAT,
    URL_TOKEN,
)

_LOGGER = logging.getLogger(__name__)

_IP_PATTERN = re.compile(r"invalid ip\s*[:：]?\s*([0-9a-fA-F:.]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class WeChatError(Exception):
    """微信接口返回的业务错误。"""

    def __init__(self, code: int, message: str) -> None:
        """初始化。"""
        self.code = code
        self.message = message
        super().__init__(f"微信接口错误 {code}: {message}")


class WeChatConnectionError(WeChatError):
    """网络层错误。"""

    def __init__(self, message: str) -> None:
        """初始化。"""
        super().__init__(-1, message)


class WeChatAuthError(WeChatError):
    """AppID / AppSecret 错误或 token 失效。"""


class WeChatIpNotAllowedError(WeChatAuthError):
    """调用方 IP 不在公众号后台的 IP 白名单中。"""

    def __init__(self, code: int, message: str, ip: str | None) -> None:
        """初始化。"""
        super().__init__(code, message)
        self.ip = ip


class WeChatNotPublisherError(WeChatError):
    """该账号不是流量主。"""


class WeChatRateLimitError(WeChatError):
    """调用频率超限。"""


def _extract_error(payload: Mapping[str, Any]) -> tuple[int, str]:
    """兼容 base_resp / errcode 两种错误结构。"""
    base = payload.get("base_resp")
    ret: Any = None
    msg: Any = None
    if isinstance(base, Mapping):
        ret = base.get("ret")
        msg = base.get("err_msg")
    if ret is None:
        ret = payload.get("errcode")
    if not msg:
        msg = payload.get("errmsg") or ""
    try:
        code = int(ret) if ret is not None else 0
    except (TypeError, ValueError):
        code = -1
    return code, str(msg)


def _raise_for_payload(payload: Mapping[str, Any]) -> None:
    """检查返回体，出错时抛出对应异常。"""
    code, message = _extract_error(payload)
    if code == 0:
        return

    if code == 40164 or "not in whitelist" in message:
        match = _IP_PATTERN.search(message)
        raise WeChatIpNotAllowedError(code, message, match.group(1) if match else None)
    if code == 2009:
        raise WeChatNotPublisherError(code, message)
    if code in (40001, 40125, 42001, 40013):
        raise WeChatAuthError(code, message)
    if code in (45009, 45011, 45033):
        raise WeChatRateLimitError(code, message)
    raise WeChatError(code, message)


# ---------------------------------------------------------------------------
# access_token 持久化缓存
# ---------------------------------------------------------------------------
class TokenStore:
    """把 access_token 存到 .storage，避免重启后重复拉取。"""

    def __init__(self, hass: HomeAssistant) -> None:
        """初始化。"""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_TOKENS
        )
        self._data: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def _async_load(self) -> dict[str, Any]:
        """按需读取缓存。"""
        if self._data is None:
            stored = await self._store.async_load()
            self._data = stored if isinstance(stored, dict) else {}
        return self._data

    async def async_get(self, appid: str) -> dict[str, Any] | None:
        """读取某个账号的缓存 token。"""
        async with self._lock:
            data = await self._async_load()
            entry = data.get(appid)
            if not isinstance(entry, dict):
                return None
            if float(entry.get("expires_at", 0)) <= time.time():
                return None
            return entry

    async def async_set(self, appid: str, token: str, expires_at: float) -> None:
        """写入并持久化。"""
        async with self._lock:
            data = await self._async_load()
            data[appid] = {"token": token, "expires_at": expires_at}
            await self._store.async_save(data)

    async def async_remove(self, appid: str) -> None:
        """移除某个账号的缓存。"""
        async with self._lock:
            data = await self._async_load()
            if data.pop(appid, None) is not None:
                await self._store.async_save(data)


def async_get_token_store(hass: HomeAssistant) -> TokenStore:
    """整个集成共用一个 TokenStore，避免多个账号互相覆盖缓存文件。"""
    domain_data: dict[str, Any] = hass.data.setdefault(DOMAIN, {})
    store = domain_data.get("token_store")
    if not isinstance(store, TokenStore):
        store = TokenStore(hass)
        domain_data["token_store"] = store
    return store


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class WeChatPublisherApi:
    """单个账号的流量主数据客户端。"""

    def __init__(
        self,
        session: ClientSession,
        appid: str,
        appsecret: str,
        tokens: TokenStore,
    ) -> None:
        """初始化。"""
        self._session = session
        self.appid = appid
        self._appsecret = appsecret
        self._tokens = tokens
        self._token_lock = asyncio.Lock()

    # -- 基础请求 ---------------------------------------------------------
    async def _async_request(
        self, url: str, params: Mapping[str, Any]
    ) -> dict[str, Any]:
        """发起 GET 请求并解析 JSON。"""
        try:
            async with self._session.get(
                url,
                params=params,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                raw = await response.text()
        except (ClientError, TimeoutError) as err:
            raise WeChatConnectionError(f"无法连接微信接口: {err}") from err

        try:
            payload = json.loads(raw)
        except ValueError as err:
            raise WeChatConnectionError(
                f"微信接口返回了非 JSON 内容（HTTP {response.status}）: {raw[:180]}"
            ) from err

        if not isinstance(payload, dict):
            raise WeChatConnectionError(f"微信接口返回了非预期结构: {raw[:180]}")
        return payload

    # -- token ------------------------------------------------------------
    async def async_get_access_token(self, *, force: bool = False) -> str:
        """获取 access_token（带本地缓存）。"""
        async with self._token_lock:
            if not force:
                cached = await self._tokens.async_get(self.appid)
                if cached and cached.get("token"):
                    return str(cached["token"])

            payload = await self._async_request(
                URL_TOKEN,
                {
                    "grant_type": "client_credential",
                    "appid": self.appid,
                    "secret": self._appsecret,
                },
            )
            _raise_for_payload(payload)

            token = payload.get("access_token")
            if not token:
                raise WeChatAuthError(0, "接口未返回 access_token")
            expires_in = int(payload.get("expires_in") or 7200)
            await self._tokens.async_set(
                self.appid,
                str(token),
                time.time() + max(expires_in - TOKEN_EXPIRY_MARGIN, 60),
            )
            return str(token)

    async def async_invalidate_token(self) -> None:
        """废弃缓存的 token。"""
        await self._tokens.async_remove(self.appid)

    # -- 广告汇总数据 -----------------------------------------------------
    async def _async_fetch_adpos(
        self, action: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        """按分页拉取某个 action 的广告位汇总数据。"""
        token = await self.async_get_access_token()
        rows: list[dict[str, Any]] = []
        page = 1
        token_retries = 0

        while page <= MAX_PAGES:
            payload = await self._async_request(
                URL_STAT,
                {
                    "action": action,
                    "access_token": token,
                    "page": page,
                    "page_size": PAGE_SIZE,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            )
            try:
                _raise_for_payload(payload)
            except WeChatAuthError as err:
                if err.code not in TOKEN_ERROR_CODES or token_retries >= 1:
                    raise
                token_retries += 1
                _LOGGER.debug("access_token 失效，强制刷新后重试 (%s)", err.code)
                await self.async_invalidate_token()
                token = await self.async_get_access_token(force=True)
                page = 1
                rows.clear()
                continue

            batch = payload.get("list") or []
            if not isinstance(batch, list):
                batch = []
            rows.extend(item for item in batch if isinstance(item, dict))

            try:
                total = int(payload.get("total_num") or 0)
            except (TypeError, ValueError):
                total = 0
            if not batch or len(rows) >= total:
                break
            page += 1

        return rows

    async def async_get_daily_stats(
        self, start: date, end: date
    ) -> tuple[list[dict[str, Any]], str]:
        """拉取 [start, end] 区间内的广告位日数据。

        返回 (原始行列表, 实际生效的 action)，老账号会自动降级到旧版接口。
        """
        if (end - start).days > MAX_SPAN_DAYS:
            start = date.fromordinal(end.toordinal() - MAX_SPAN_DAYS)

        try:
            return await self._async_fetch_adpos(
                ACTION_ADPOS_GENERAL, start, end
            ), ACTION_ADPOS_GENERAL
        except (WeChatAuthError, WeChatConnectionError, WeChatRateLimitError):
            raise
        except WeChatError as err:
            _LOGGER.debug("新版接口失败(%s)，尝试旧版接口 %s", err, ACTION_ADPOS_GENERAL_LEGACY)
            rows = await self._async_fetch_adpos(
                ACTION_ADPOS_GENERAL_LEGACY, start, end
            )
            return rows, ACTION_ADPOS_GENERAL_LEGACY

    # -- 结算数据 ---------------------------------------------------------
    async def async_get_settlement(self, start: date, end: date) -> dict[str, Any]:
        """拉取结算/累计收入数据。"""
        token = await self.async_get_access_token()
        page = 1
        merged: dict[str, Any] = {}
        settlement_list: list[dict[str, Any]] = []

        while page <= MAX_PAGES:
            payload = await self._async_request(
                URL_STAT,
                {
                    "action": ACTION_SETTLEMENT,
                    "access_token": token,
                    "page": page,
                    "page_size": PAGE_SIZE,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                },
            )
            _raise_for_payload(payload)
            if not merged:
                merged = {
                    "body": payload.get("body"),
                    "revenue_all": payload.get("revenue_all") or 0,
                    "settled_revenue_all": payload.get("settled_revenue_all") or 0,
                    "penalty_all": payload.get("penalty_all") or 0,
                }
            batch = payload.get("settlement_list") or []
            if isinstance(batch, list):
                settlement_list.extend(
                    item for item in batch if isinstance(item, dict)
                )
            try:
                total = int(payload.get("total_num") or 0)
            except (TypeError, ValueError):
                total = 0
            if not batch or len(settlement_list) >= total:
                break
            page += 1

        merged["settlement_list"] = settlement_list
        return merged

    # -- 配置流程校验 ------------------------------------------------------
    async def async_validate(self) -> dict[str, Any]:
        """校验 AppID/AppSecret 与流量主权限。"""
        await self.async_get_access_token(force=True)
        today = date.today()
        rows, action = await self.async_get_daily_stats(
            date.fromordinal(today.toordinal() - 7), today
        )
        return {"action": action, "rows": len(rows)}
