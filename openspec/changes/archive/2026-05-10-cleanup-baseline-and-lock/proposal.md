## Why

仓库自 2025-08-26 初始 commit 之后 8 个月未提交，积累了 10 个修改、1 个删除、21 个未跟踪条目。脏改动里 docker-compose.yml 引用了本地不存在的 `docker-vpn-gateway` 项目网络与 `/Volumes/磁盘/...` 不存在的卷路径，导致 `docker compose up` 无法启动；`src/config.py` 文件与新增的 `src/config/` 目录构成 Python 包导入冲突；`src/enhanced_collectors/` 与 `src/collectors/` 是同一职责的两套代码。这些遗留问题阻碍后续业务模块（成交量异常监控 + Telegram 告警）的开发。

本次 change 把这堆脏改动梳理为干净、可部署、可演进的 baseline，并通过 spec 文件锁定底座契约，让后续 change 有稳定起点。

## What Changes

- **修复** `docker-compose.yml`：删除引用幻象项目的 VPN 网络块；ClickHouse 卷路径参数化为 `${CLICKHOUSE_DATA_DIR:-/Volumes/FORGE/data/clickhouse}`；日志卷恢复相对路径 `./logs:/app/logs`；data-service 的 VPN 代理环境变量删除（本地不需要、生产用 host network 或 sidecar 解决）
- **解冲突** `src/config/` 目录与 `src/config.py` 文件：将 `src/config/collectors.yml` 移动到 `config/collectors.yml`，删除 `src/config/` 空目录
- **新增** `.gitignore`：忽略 `logs/`、`data/`、`.env`、`__pycache__/`、`*.pyc`、`.claude/settings.local.json`、`*.log`、`.DS_Store`
- **创建** 数据卷父目录 `/Volumes/FORGE/data/clickhouse`（首次部署用，文档化即可，不在 commit 内）
- **保留** 以下脏改动并 commit 锁定为 baseline：依赖更新（tenacity / prometheus-client / watchdog / joblib）、采集器增强（ms 时间戳 + 重连优化）、ClickHouse 内存优化、main.py 日志轮转、`MAX_GAP_MINUTES` 调整、`src/api/freqtrade.py`、`config/`、`scripts/`、`tests/`、`docs/`
- **隔离** 重复或过时代码到 `docs/legacy/`：`src/enhanced_collectors/` 整目录、`src/utils/enhanced/` 整目录、`examples/enhanced_architecture_demo.py`
- **隔离** 备用参考代码到 `docs/reference/`：`src/validators/crypto_data_validator.py`（Qlib 风格数据验证器，detection 模块设计可参考）
- **新建** `baseline-infrastructure` capability spec，把"项目底座"形式化为可被引用的契约：服务清单、卷与网络要求、必需环境变量、健康检查、目录约定、新模块如何接入
- **NOT IN SCOPE**：业务模块（classification / detection / alerts）由后续三个独立 change 负责；本次不引入任何新业务逻辑、不写新业务代码、不改业务行为

## Capabilities

### New Capabilities

- `baseline-infrastructure`: 描述本项目的运行时底座契约——docker-compose 服务（ClickHouse + data-service）、卷与网络约定、必需的环境变量、健康检查规约、目录布局、新业务模块的接入约定。是后续所有业务 change 的依赖基准。

### Modified Capabilities

（无，本项目之前没有 specs，本次为首批 spec）

## Impact

**Affected code**:
- `docker-compose.yml`（修改，删 VPN 块 + 改卷路径）
- `src/config/`（删除目录）
- `config/collectors.yml`（新建，从 `src/config/collectors.yml` 移过来）
- `.gitignore`（新建）
- `docs/legacy/enhanced_collectors/`、`docs/legacy/utils_enhanced/`、`docs/legacy/examples/`（新建子树，迁入旧代码）
- `docs/reference/crypto_data_validator.py`（新建，迁入备用代码）

**Affected APIs**: 无（本次不动业务代码与 API）

**Affected dependencies**: 无（已新增的 tenacity/prometheus-client/watchdog/joblib 在脏改动中保留）

**Affected systems**:
- 本地 docker compose 可成功启动（`docker compose up -d` 不再报缺失网络/路径错误）
- 后续 change 可以引用 `baseline-infrastructure` spec 的契约（如"新模块必须接到 main.py 的 lifespan、必须暴露 /health 子探针"）

**Risk**: 低。本次只动配置、移文件、加 ignore，不改业务行为。回滚成本 = `git revert` 一次。
