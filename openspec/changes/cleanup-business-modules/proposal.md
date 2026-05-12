## Why

`data-service` 仓库当前处于**双跑状态**：
- 仓内 `src/{classification,detection,alerts}/` 三个业务模块仍随 `data-service` 进程内消费同进程内存中的 K 线对象（旧路径）
- 同时 `volume-monitor` + `telegram-bot` 两个独立 repo 已通过 NATS event bus 订阅同一份 K 线，跑出完整 e2e 链路（新路径）

按 `docs/superpowers/specs/2026-05-12-data-service-split-architecture-design.md` 第 5 阶段，新链路稳定后必须执行 cleanup，让 `data-service` 退化成**纯数据中继**：只负责 WS 采集 → ClickHouse 落库 → NATS publish；业务逻辑全部由外部消费者承担。

不做 cleanup 的代价：
1. **职责泄漏** — `data-service` 的 `.env.example` 和 `src/config.py` 仍持有 `TELEGRAM_BOT_TOKEN` / `DETECTION_THRESHOLDS` 等业务配置，违反"采集进程不接触业务参数"的拆分原则
2. **重复检测、重复告警** — 同一根 K 线被新旧两条链路同时检测，可能产生重复 Telegram 推送
3. **镜像膨胀** — `freqtrade-data-service-data-service:latest` 当前 2.07 GB（含 detection / classification / alerts 依赖），cleanup 后预计 < 1 GB
4. **协议不一致** — 旧路径用 process-internal Python 对象，新路径用 NATS JSON event，两套 schema 维护成本高

cooling-off period (≥3 天) 已在 5/13 提前结束 —— 双跑 24h+ e2e 已实测通过（`e2e-test-v2-1778528640: status=sent, attempts=1, elapsed=1510ms`），用户决策直接 cleanup。

## What Changes

### 移除能力 `volume-classification` (REMOVED capability spec)
- 整 capability `volume-classification` 从 `openspec/specs/` 移除
- 对应代码：`src/classification/` 整目录删除
- 对应配置：`config.py` 中 `CLASSIFICATION_REFRESH_HOURS` 字段删除
- 对应路由：`/api/v1/tiers` 等 classification API 全部下线（外部 monitor 已自带分类逻辑）

### 移除能力 `volume-detection` (REMOVED capability spec)
- 整 capability `volume-detection` 从 `openspec/specs/` 移除
- 对应代码：`src/detection/` 整目录删除 + `src/scheduler.py`（仅服务于 detection 周期）删除
- 对应配置：`config.py` 中 `DETECTION_*` 共 5 个字段删除
- 对应路由：`/api/v1/detection/*` 全部下线

### 移除能力 `telegram-alerts` (REMOVED capability spec)
- 整 capability `telegram-alerts` 从 `openspec/specs/` 移除
- 对应代码：`src/alerts/` 整目录删除
- 对应配置：`config.py` 中 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_PARSE_MODE` / `TELEGRAM_PROXY_URL` 删除
- 对应路由：`/api/v1/alerts/*` 全部下线

### 修改能力 `baseline-infrastructure`
- **MODIFY** Requirement: Required Environment Variables — 删除 `.env.example` 中 Telegram / Detection / Alerts / Classification 配置块
- **MODIFY** Requirement: Module Wiring Convention — Health sub-probe 列表从 `database, classification, detection, alerts, ws_collectors, recovery, nats` 缩减为 `database, ws_collectors, recovery, nats`

### 不动
- `src/events/`（NATS publisher）保留 — data-service 的核心新职责
- `src/storage/`、`src/collectors/`、`src/services/recovery.py`、`src/api/routes.py` 全部保留 — 数据采集 + 落库 + 查询是 data-service 的本职
- `src/utils/`、ClickHouse schema、recovery service 全部保留
- `volume-monitor` + `telegram-bot` 两个外部 repo 不动 — 它们已通过 NATS 接管业务

## Impact

**Affected code (data-service)**:
- 删除目录：`src/classification/`、`src/detection/`、`src/alerts/`
- 删除文件：`src/scheduler.py`
- 修改文件：`src/main.py`（删 9 处 import + lifespan startup 中 Classifier/Detector/Notifier 实例化 + scheduler 启动 + 3 处 `app.include_router` + health sub-probe 中 3 个字段）
- 修改文件：`src/config.py`（删 10 个业务配置字段）
- 修改文件：`.env.example`（删 Telegram / Detection / Alerts 块）
- 删除测试 11 个：`tests/unit/test_{notifier,detector,telegram_client,detection_api,scheduler,formatter,backfill,classifier}.py` + `tests/integration/test_{classification_e2e,detection_e2e,full_pipeline}.py`
- 修改测试：`tests/unit/test_health_endpoint.py`（删 classification/detection/alerts sub-probe 断言）

**Affected production state (jarvis)**:
- `data-service` 镜像 rebuild：2.07 GB → 预计 < 1 GB
- `data-service` 容器内存占用下降（不再实例化 Classifier / Detector / Notifier / scheduler）
- API 表面缩减：`/api/v1/tiers`、`/api/v1/detection/*`、`/api/v1/alerts/*` 全部 404
- ClickHouse `telegram_audit` 表保留（telegram-bot 仍在写）；`alerts_log` 旧表如果存在则保留 read-only

**Affected external**:
- `volume-monitor` + `telegram-bot` 不变 — 它们订阅 NATS，不依赖 data-service 内部 API
- 任何还在调用 `data-service:8000/api/v1/{tiers,detection,alerts}` 的下游会收到 404 — 检查后无下游

**Risk**: 低-中。
- **可回滚** — git revert 单 commit 即可恢复双跑状态
- 不可逆操作只有"删代码"，但 git history 保留
- 风险点：万一 monitor 在 cleanup 后 24h 内出现行为漂移（与原 detection 逻辑不一致），需要 git revert 拉回旧路径；由于双跑期间已实测一致，概率低
- 不影响 ClickHouse 数据（不删表、不删行）
- 不影响 NATS stream（保留 24h messages 不动）
