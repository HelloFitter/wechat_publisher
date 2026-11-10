# 微信流量主 Home Assistant 集成

把**公众号 / 小程序 / 小游戏**的流量主收益数据接入 Home Assistant：昨日收入、曝光、点击、eCPM、近 7 天 / 30 天 / 本月汇总、累计结算收入一站看全，还能用自动化每天早上把日报推到手机上。

> 数据来源为微信官方接口（`https://api.weixin.qq.com/publisher/stat`），AppSecret 只保存在你自己的 Home Assistant 里，不经过任何第三方服务器。

## ✨ 功能

- **多账号**：每个公众号 / 小程序添加一个条目，互不影响
- **传感器**（每个账号）：
  - 昨日收入（含曝光量、点击量、点击率、eCPM、拉取量、环比、广告位构成、每日收益历史等属性）
  - 近 7 天收入 / 近 30 天收入 / 本月收入
  - 累计收入 / 已结算收入 / 待结算收入（来自结算接口）
  - 数据日期（诊断：微信数据更新到了哪一天）
- **自动降级**：老账号新版接口不可用时自动切换旧版接口
- **access_token 缓存**：持久化到 `.storage`，重启不重复请求
- **服务**：`wechat_publisher.get_report` 生成可直接推送的报表文本；`wechat_publisher.refresh` 手动刷新
- **自动重新授权**：AppSecret 失效时 HA 自动弹出重新授权界面
- **诊断**：设置 → 设备与服务 → 微信流量主 → 诊断（自动隐藏 AppSecret）

## 📋 前置条件

1. 账号已开通**流量主**
2. 拿到该账号的 **AppID / AppSecret**（公众平台 → 设置与开发 → 基本配置）
3. ⚠️ **必须把运行 Home Assistant 的服务器出口 IP 加入该账号的 IP 白名单**（设置与开发 → 基本配置 → IP 白名单）。配置流程里会直接显示被微信检测到的 IP，照着填即可。
4. Home Assistant ≥ 2024.6

## 📦 安装

### 方式一：HACS（推荐）

1. HACS → 右上角 ⋮ → 自定义存储库
2. 仓库地址填本仓库，类别选 **Integration（集成）**
3. 安装「微信流量主」后**重启 Home Assistant**

### 方式二：手动

把 `custom_components/wechat_publisher` 整个目录复制到 HA 配置目录的 `custom_components/` 下，重启 HA：

```text
<config_dir>/custom_components/wechat_publisher/
├── __init__.py
├── api.py
├── coordinator.py
├── config_flow.py
├── sensor.py
├── diagnostics.py
├── manifest.json
├── strings.json
├── translations/
└── brand/
```

## 🚀 配置

设置 → 设备与服务 → **添加集成** → 搜索「**微信流量主**」：

1. 填写账号名称（自定义）、账号类型（公众号 / 小程序）、AppID、AppSecret
2. 点击提交，集成会真实调用微信接口校验（包含 IP 白名单、流量主权限检查）
3. 有多个账号就再添加一次，每个账号一个条目

### 选项（点击条目 → 配置）

| 选项 | 默认 | 说明 |
| --- | --- | --- |
| 更新间隔 | 1 小时 | 流量主数据每天上午更新一次，没必要设太短；最小 0.2 小时 |
| 历史数据天数 | 30 | 内存中缓存的收益历史，影响 30 天汇总与趋势图，最大 90 天（微信接口限制） |
| 获取结算数据 | 开 | 关闭后「累计 / 已结算 / 待结算收入」变为未知 |

## 📊 实体一览

| 实体 | 单位 | 说明 |
| --- | --- | --- |
| `sensor.xxx_zuo_ri_shou_ru` | ¥ (CNY) | 微信已公布的最新一天收益 |
| `sensor.xxx_zuo_ri_bao_guang_liang` | 次 | 昨日曝光量 |
| `sensor.xxx_zuo_ri_dian_ji_liang` | 次 | 昨日点击量 |
| `sensor.xxx_zuo_ri_dian_ji_lv` | % | 昨日点击率 |
| `sensor.xxx_zuo_ri_ecpm` | ¥ | 昨日千次曝光收益 |
| `sensor.xxx_zuo_ri_guang_gao_la_qu_liang` | 次 | 昨日广告拉取量 |
| `sensor.xxx_jin_7_tian_shou_ru` | ¥ | 近 7 天收入 |
| `sensor.xxx_jin_30_tian_shou_ru` | ¥ | 近 30 天收入 |
| `sensor.xxx_ben_yue_shou_ru` | ¥ | 本月累计（自然月） |
| `sensor.xxx_lei_ji_shou_ru` | ¥ | 累计收入（结算口径） |
| `sensor.xxx_yi_jie_suan_shou_ru` | ¥ | 已结算收入 |
| `sensor.xxx_dai_jie_suan_shou_ru` | ¥ | 待结算收入 |
| `sensor.xxx_shu_ju_ri_qi` | - | 诊断：数据更新到哪天 |

> 收入单位是**元**；微信接口返回的是「分」，集成已自动换算。

### 「昨日收入」的常用属性

| 属性 | 说明 |
| --- | --- |
| `date` | 本数值对应的收益日期 |
| `history` | `{"2025-01-01": 12.34, ...}` 每日收益（元），可直接画图 |
| `slots` | 按广告位拆分的收益、曝光、点击（中文名） |
| `change_pct` | 相对前一天的收益变化（%） |
| `exposure` / `clicks` / `click_rate` / `ecpm` / `req_succ_count` | 当日核心指标 |

## 🤖 自动化：每天早上推送日报

```yaml
automation:
  - alias: 微信流量主每日播报
    mode: single
    triggers:
      - trigger: time
        at: "09:30:00"
    actions:
      - action: wechat_publisher.get_report
        data:
          days: 1          # 1=昨日，7/30=近 N 天
        response_variable: report
      - action: notify.mobile_app_your_phone
        data:
          title: "📊 微信流量主日报"
          message: "{{ report.text }}"
```

`report.text` 的效果：

```text
📊 微信流量主日报 · 2025-01-06
• 我的公众号：¥12.34（曝光 6,000｜点击 110｜CTR 1.83%）
• 我的小程序：¥3.20
合计：¥15.54
```

也可以在 Developer Tools → Actions 里手动调用 `wechat_publisher.get_report`（返回响应），或用 `wechat_publisher.refresh` 立即刷新。

## 🖼️ 仪表盘示例

需要先通过 HACS 安装 [apexcharts-card](https://github.com/RomRider/apexcharts-card) 和 [stack-in-card](https://github.com/custom-cards/stack-in-card)（可选）：

```yaml
type: custom:apexcharts-card
header:
  title: 微信流量主 · 每日收益
  show: true
graph_span: 30d
span:
  start: day
  offset: -30d
series:
  - entity: sensor.xxx_zuo_ri_shou_ru
    name: 收益（元）
    type: area
    attribute: history
    data_generator: |
      return Object.entries(entity.attributes.history).map(
        ([d, v]) => [new Date(d).getTime(), v]
      );
```

多账号合计收入可以用模板传感器：

```yaml
template:
  - sensor:
      - name: 微信流量主合计收入
        unit_of_measurement: CNY
        device_class: monetary
        state: >
          {{ states('sensor.gong_zhong_hao_zuo_ri_shou_ru') | float(0, none)
             + states('sensor.xiao_cheng_xu_zuo_ri_shou_ru') | float(0, none)
             | round(2) }}
```

## ❓ 常见问题

**提示「服务器 IP 不在白名单中」**
到公众平台后台「设置与开发 → 基本配置 → IP 白名单」加上配置界面里显示的那个 IP。服务器换了网络（如换了宽带 / 部署位置）后要重新加。

**提示「该账号尚未开通流量主」**
AppSecret 正确但账号没开通流量主，去公众平台后台开通后再添加。

**昨日收入一直是 0 或不变**
微信一般每天上午 9~11 点更新前一天数据，属于正常延迟；「数据日期」传感器能看到微信实际更新到哪天。

**接口调用频率超限（45009）**
把更新间隔调大（默认 1 小时足够）。微信对广告数据接口有频控，多账号不要把间隔设得过小。

**近 30 天收入看起来偏小**
「历史数据天数」默认只缓存 30 天，且接口一次最多查 90 天；想要准确的 30/90 天数据，把选项调到 90。

**老公众号提示接口不可用**
集成会自动降级到旧版接口 `publisher_biz_adpos_general`，无需手动处理；若仍失败请提交 issue。

## 🧪 开发与测试

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install pytest-homeassistant-custom-component==0.13.205
pytest
```

测试覆盖：token 缓存与失效重试、分页、新旧接口降级、IP 白名单 / 鉴权 / 流量主错误映射、配置流程（成功 / 失败 / 重复 / 重新授权）、传感器数值与属性、报表服务、卸载。

## ⚠️ 免责声明

本集成调用微信官方公开接口，仅做数据展示；AppSecret 属于高敏感信息，请妥善保管服务器安全。若微信调整接口导致失效，请提 issue 反馈。
