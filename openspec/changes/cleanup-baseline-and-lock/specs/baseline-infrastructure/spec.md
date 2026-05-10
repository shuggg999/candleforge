## ADDED Requirements

### Requirement: Service Topology

The project SHALL ship a `docker-compose.yml` at repo root that defines exactly two services for the data-service runtime: `clickhouse` (image `clickhouse/clickhouse-server:latest`) and `data-service` (built from local `Dockerfile`). The compose file MUST NOT depend on any docker network or volume that lives outside this repository or assumes a sibling project is running.

#### Scenario: Compose starts on a fresh checkout

- **WHEN** a developer clones the repo, sets `CLICKHOUSE_DATA_DIR` to a writable absolute path, and runs `docker compose up -d`
- **THEN** both `clickhouse` and `data-service` containers reach `Up (healthy)` within 90 seconds without referencing any external network or sibling project

#### Scenario: No phantom external network

- **WHEN** an automated check parses `docker-compose.yml`
- **THEN** the file MUST NOT declare any `external: true` network and MUST NOT reference `docker-vpn-gateway_vpn_network` or any other network defined outside this repo

### Requirement: Parameterized ClickHouse Persistence Path

The ClickHouse data volume mount path SHALL be parameterized via the `CLICKHOUSE_DATA_DIR` environment variable, defaulting to `/Volumes/FORGE/data/clickhouse` on macOS dev machines and overridable on Linux production hosts (e.g. Bulgarian VPS) without editing `docker-compose.yml`.

#### Scenario: Override path on production host

- **WHEN** the operator sets `CLICKHOUSE_DATA_DIR=/data/clickhouse` in `.env` on the production host
- **THEN** `docker compose up -d` mounts the production path without source-code changes

#### Scenario: Default path on local dev

- **WHEN** the developer leaves `CLICKHOUSE_DATA_DIR` unset
- **THEN** the compose file binds to `/Volumes/FORGE/data/clickhouse`

### Requirement: Required Environment Variables

The project SHALL declare a `.env.example` file enumerating every environment variable consumed by the data-service runtime, with safe defaults or clear placeholder values. Required keys include: `CLICKHOUSE_HOST`, `CLICKHOUSE_PORT`, `CLICKHOUSE_DATABASE`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `ENABLED_EXCHANGES`, `LOG_LEVEL`, `RECOVERY_CHECK_INTERVAL`, `MAX_GAP_MINUTES`, `MAX_SYMBOLS_PER_WS_CONNECTION`. Future capabilities (alerts, detection thresholds, etc.) SHALL extend `.env.example` rather than introducing undocumented runtime knobs.

#### Scenario: Operator audits required config

- **WHEN** an operator opens `.env.example`
- **THEN** every variable referenced by `src/config.py` and any new module under `src/` is listed with either a default value or a `# REQUIRED` placeholder

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

#### Scenario: Adding a new module surfaces in /health

- **WHEN** a developer adds `src/classification/` following this convention
- **THEN** `GET /api/v1/health` returns a JSON body containing `"classification"` as a key with health status, and the lifespan `startup` event has wired the module's factory

#### Scenario: Module side-effects deferred until startup

- **WHEN** `python -c "import src.main"` runs on a machine with no network and no ClickHouse
- **THEN** the import succeeds without raising connection errors or starting threads

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
