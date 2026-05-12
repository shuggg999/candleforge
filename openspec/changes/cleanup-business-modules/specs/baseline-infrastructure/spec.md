## MODIFIED Requirements

### Requirement: Required Environment Variables

The project SHALL declare a `.env.example` file enumerating every environment variable consumed by the data-service runtime, with safe defaults or clear placeholder values. Required keys include: `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `ENABLED_EXCHANGES`, `LOG_LEVEL`, `RECOVERY_CHECK_INTERVAL`, `MAX_GAP_MINUTES`, `MAX_SYMBOLS_PER_WS_CONNECTION`, `NATS_URL`, `NATS_ENABLE`. The `.env.example` MUST NOT contain Telegram, alert, detection, or classification configuration — those belong in their respective downstream service repos (`telegram-bot/.env.example` and `volume-monitor/.env.example`).

#### Scenario: Operator audits required config

- **WHEN** an operator opens `.env.example`
- **THEN** every variable referenced by `src/config.py` is listed with either a default value or a `# REQUIRED` placeholder

#### Scenario: NATS keys documented

- **WHEN** an operator searches `.env.example` for `NATS`
- **THEN** the file shows `NATS_URL` (default `nats://nats:4222` for in-cluster, override for jarvis hostnames) and `NATS_ENABLE` (default `true`, set to `false` to no-op the publisher)

#### Scenario: No business module config leakage

- **WHEN** an operator searches `.env.example` for `TELEGRAM` / `DETECTION` / `CLASSIFICATION` / `ALERTS`
- **THEN** zero matches are found; `.env.example` MUST instead include a comment pointing to `volume-monitor/.env.example` and `telegram-bot/.env.example`

### Requirement: Module Wiring Convention

Any new business module placed under `src/<module>/` SHALL expose:
1. An `__init__.py` that re-exports the module's public API,
2. A factory or class that can be wired into `src/main.py`'s FastAPI lifespan (`startup` / `shutdown`), and
3. A health probe contributing to a sub-key in the `/api/v1/health` JSON response (e.g. `{"clickhouse": "ok", "<module>": "ok"}`).

Modules MUST NOT start background threads or open network sockets at import time.

**单一 /health 路由**：所有 `/api/v1/health` 实现 MUST 集中在 `src/main.py` 中的单一 endpoint。`src/api/routes.py` 等 router 文件 MUST NOT 注册同路径的 endpoint —— FastAPI 先注册者优先，重复注册会**静默覆盖** main.py 的实现，使 sub-probe 失效。

**Health sub-probe 完整列表** (本仓 data-service 进程暴露)：`database`, `ws_collectors`, `recovery`, `nats`。`nats` sub-probe shape 详见 `nats-event-bus` capability spec。业务模块 sub-probe（classification / detection / alerts）SHALL NOT 出现在本仓 health body 中 —— 已下线，由独立 `volume-monitor:8200/api/v1/health` 和 `telegram-bot:8300/api/v1/health` 暴露。

#### Scenario: Adding a new module surfaces in /health

- **WHEN** a developer adds a new module `src/<m>/` following this convention
- **THEN** `GET /api/v1/health` returns a JSON body containing `<m>` as a key with health status, and the lifespan `startup` event has wired the module's factory

#### Scenario: Module side-effects deferred until startup

- **WHEN** `python -c "import src.main"` runs on a machine with no network and no ClickHouse
- **THEN** the import succeeds without raising connection errors or starting threads

#### Scenario: No duplicate /health route silently overrides main.py

- **WHEN** automated check parses FastAPI app routes
- **THEN** exactly one handler is registered for path `/api/v1/health`, defined in `src/main.py`, and `src/api/routes.py` does not declare `@router.get("/health")`

#### Scenario: Sub-probe failure does not crash health endpoint

- **WHEN** a sub-probe function (e.g. `nats_publisher.health()`) raises an unexpected exception
- **THEN** `/api/v1/health` still returns a JSON body with that sub-probe's value set to `{"status": "failed", "reason": <error string>}` and overall HTTP status 503, NOT a 500 internal server error

#### Scenario: NATS sub-probe present from this change onward

- **WHEN** `GET /api/v1/health` is hit after `introduce-nats-event-bus` is archived
- **THEN** `body.details.nats` exists with the shape defined in `nats-event-bus` capability spec, regardless of whether `NATS_ENABLE` is true or false

#### Scenario: Business module sub-probes absent after cleanup

- **WHEN** `GET /api/v1/health` is hit after `cleanup-business-modules` is archived
- **THEN** `body.details` SHALL contain exactly the keys `{database, ws_collectors, recovery, nats}` and SHALL NOT contain `classification`, `detection`, or `alerts`
