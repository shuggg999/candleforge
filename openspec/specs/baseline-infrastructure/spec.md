# baseline-infrastructure Specification

## Purpose
TBD - created by archiving change cleanup-baseline-and-lock. Update Purpose after archive.
## Requirements
### Requirement: Service Topology

The project SHALL ship a `docker-compose.yml` at repo root that defines exactly three services for the data-service runtime: `clickhouse` (image `clickhouse/clickhouse-server:latest`), `data-service` (built from local `Dockerfile`), and `nats` (image `nats:2.10-alpine` running with JetStream enabled). The compose file MUST NOT depend on any docker network or volume that lives outside this repository or assumes a sibling project is running.

#### Scenario: Compose starts on a fresh checkout

- **WHEN** a developer clones the repo, sets `CLICKHOUSE_DATA_DIR` to a writable absolute path, and runs `docker compose up -d`
- **THEN** all three containers — `clickhouse`, `nats`, and `data-service` — reach `Up (healthy)` within 90 seconds without referencing any external network or sibling project

#### Scenario: No phantom external network

- **WHEN** an automated check parses `docker-compose.yml`
- **THEN** the file MUST NOT declare any `external: true` network and MUST NOT reference `docker-vpn-gateway_vpn_network` or any other network defined outside this repo

#### Scenario: NATS volume persists across container recreation

- **WHEN** `docker compose down nats && docker compose up -d nats` is invoked
- **THEN** the `nats-data` named volume retains JetStream files so the `OHLCV` stream and any unconsumed messages within `MaxAge` are preserved

### Requirement: Parameterized ClickHouse Persistence Path

The ClickHouse data volume mount path SHALL be parameterized via the `CLICKHOUSE_DATA_DIR` environment variable, defaulting to `./data/clickhouse` (relative to the repo root) and overridable to an absolute path on production hosts without editing `docker-compose.yml`.

#### Scenario: Override path on production host

- **WHEN** the operator sets `CLICKHOUSE_DATA_DIR=/data/clickhouse` in `.env` on the production host
- **THEN** `docker compose up -d` mounts the production path without source-code changes

#### Scenario: Default path on local dev

- **WHEN** the developer leaves `CLICKHOUSE_DATA_DIR` unset
- **THEN** the compose file binds to `./data/clickhouse` relative to the repo root

### Requirement: Required Environment Variables

The project SHALL declare a `.env.example` file enumerating every environment variable consumed by the data-service runtime, with safe defaults or clear placeholder values. Required keys include: `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `ENABLED_EXCHANGES`, `LOG_LEVEL`, `RECOVERY_CHECK_INTERVAL`, `MAX_GAP_MINUTES`, `MAX_SYMBOLS_PER_WS_CONNECTION`, `NATS_URL`, `NATS_ENABLE`. The `.env.example` MUST NOT contain Telegram, alert, detection, or classification configuration — those belong in their respective downstream service repos (`telegram-bot/.env.example` and `volume-monitor/.env.example`).

#### Scenario: Operator audits required config

- **WHEN** an operator opens `.env.example`
- **THEN** every variable referenced by `src/config.py` is listed with either a default value or a `# REQUIRED` placeholder

#### Scenario: NATS keys documented

- **WHEN** an operator searches `.env.example` for `NATS`
- **THEN** the file shows `NATS_URL` (default `nats://nats:4222` for in-cluster service-name resolution, override for external host:port) and `NATS_ENABLE` (default `true`, set to `false` to no-op the publisher)

#### Scenario: No business module config leakage

- **WHEN** an operator searches `.env.example` for `TELEGRAM` / `DETECTION` / `CLASSIFICATION` / `ALERTS`
- **THEN** zero matches are found; `.env.example` MUST instead include a comment pointing to `volume-monitor/.env.example` and `telegram-bot/.env.example`

### Requirement: Health Check Per Service

Every long-running container in `docker-compose.yml` SHALL declare a `healthcheck` block with a non-trivial probe (HTTP ping, port probe, or domain-specific assertion) and a retry budget. The `data-service` container's healthcheck MUST hit `GET /api/v1/health` and SHALL return success only when ClickHouse connectivity is verified.

#### Scenario: Compose refuses to mark unhealthy service as Up

- **WHEN** `data-service` cannot reach ClickHouse
- **THEN** `docker compose ps` reports the data-service container as `Up (unhealthy)` and `depends_on: condition: service_healthy` in downstream consumers blocks startup

### Requirement: Code Layout — No Module-Package Collisions

The `src/` tree MUST NOT contain a directory and a `.py` file with the same base name (e.g. `src/config.py` and `src/config/`). All cross-module configuration files (YAML / JSON / TOML) SHALL live under the top-level `config/` directory, never under `src/`.

#### Scenario: Static check on src tree

- **WHEN** a CI lint step lists `src/` entries and groups by base name
- **THEN** no base name appears as both a file and a directory

### Requirement: Log Rotation by Default

The application entry point (`src/main.py`) SHALL configure log rotation with a maximum file size (default 50 MB) and bounded retention. Logs SHALL be written under `./logs/` when running locally and under `/app/logs` when running in the container; the path SHALL NOT be hard-coded to any specific external mount.

#### Scenario: Long-running container does not exhaust disk

- **WHEN** the data-service runs continuously for 30 days under normal load
- **THEN** total log size under `/app/logs` stays bounded by the configured rotation policy

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

### Requirement: Repository Hygiene — gitignore Coverage

The repo SHALL ship a `.gitignore` that excludes at minimum: `logs/`, `data/`, `.env`, `__pycache__/`, `*.pyc`, `.claude/settings.local.json`, `*.log`, `.DS_Store`, and any IDE workspace files.

#### Scenario: Fresh runtime artefacts stay untracked

- **WHEN** a developer runs `docker compose up`, generates logs and ClickHouse data files, and runs `git status`
- **THEN** none of `logs/`, `data/`, `.env`, or `*.pyc` files appear as untracked

### Requirement: Legacy Code Quarantine

Code that has been replaced by a current implementation but kept for reference SHALL live under `docs/legacy/<original-path>/` and MUST NOT be imported by any module under `src/`. Reference-only material (e.g. third-party-style validators kept for inspiration) SHALL live under `docs/reference/`.

#### Scenario: No live import of legacy code

- **WHEN** an automated check greps `src/` for `from docs.legacy` or `import docs.legacy`
- **THEN** zero matches are found

### Requirement: Per-Timeframe TTL Configuration

`config/clickhouse/init.sql` 的 `ohlcv_futures` 表 TTL 子句 SHALL 为每个 timeframe 单独写一条 `WHERE timeframe = '<tf>'` 表达式，禁止使用 `WHERE timeframe IN (...)` 合并多个 timeframe。任何 timeframe 的 TTL 天数变更 MUST 只影响该 timeframe，不得殃及其他 timeframe。

#### Scenario: 改 1h TTL 不应影响 15m

- **WHEN** 一个开发者把 1h TTL 从 365 改为 180 天
- **THEN** `git diff` 只显示 1h 那一条 `WHERE timeframe = '1h'` 表达式被修改，15m 那条 `WHERE timeframe = '15m'` 表达式完全未动

#### Scenario: TTL 表达式与 .env 配置一致

- **WHEN** 自动检查解析 `config/clickhouse/init.sql` 和 `.env.example` 里的 TTL 配置
- **THEN** 对每个 timeframe，init.sql 中 `WHERE timeframe = '<tf>'` 表达式的 day 数与 `.env.example` 中 `TTL_<TF>=<days>` 数字一致（1m=7, 5m=30, 15m=90, 1h=365, 4h=365, 1d=1825）

### Requirement: Production Table TTL Migration Documented

任何修改 `config/clickhouse/init.sql` 的 TTL 表达式的 change SHALL 同时在 `scripts/migrations/` 提供一条 `ALTER TABLE crypto_data.ohlcv_futures MODIFY TTL ...` SQL 脚本，使得已存在的生产部署可以在不重建表的情况下应用新 TTL。

#### Scenario: 已部署集群应用 TTL 修复

- **WHEN** 一个 change 修改 init.sql TTL 后部署到生产环境
- **THEN** `scripts/migrations/<YYYY-MM-DD>-<change-id>.sql` 包含等价的 `ALTER TABLE ... MODIFY TTL` 语句，运维只需 `clickhouse-client < scripts/migrations/<file>.sql` 即可同步生产表 TTL，无需 drop / recreate / 数据丢失

