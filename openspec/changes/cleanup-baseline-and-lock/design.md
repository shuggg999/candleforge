## Context

仓库自 2025-08-26 初始 commit 后 8 个月未提交，工作树包含 10 个修改、1 个删除、21 个未跟踪条目。在分析期间发现：

1. `docker-compose.yml` 引用 `docker-vpn-gateway_vpn_network`（外部 docker network），但本地不存在该项目 — 是从 VPS 配置抄来的死代码。
2. `docker-compose.yml` 卷路径 `/Volumes/磁盘/Projects/CryptoData/Database/clickhouse` 指向不存在的磁盘（本机仅挂载 FORGE / VAULT / Macintosh HD）；原始 commit 的路径 `/Volumes/磁盘/freqtrade-data-service/clickhouse` 同样不存在 → 项目从未在本机跑通过。
3. `src/config.py` 与未跟踪的 `src/config/` 目录构成 Python 模块冲突：`import src.config` 行为不可预测。
4. `src/enhanced_collectors/` 与 `src/collectors/` 是两套同职责实现，重复维护成本高。
5. 部署拓扑已确认：本地 Mac 只跑业务代码 + pytest；采集器 + ClickHouse 部署到保加利亚的"贾维斯"机器（本地为美国节点，无法直连 Binance）。

## Goals / Non-Goals

**Goals**:
- 一次 commit 锁定干净 baseline，工作树彻底干净
- 删除所有跑不起来的死代码（VPN 块、不存在的卷路径）
- 解 Python 模块/包命名冲突
- 把"项目底座"形式化为 `baseline-infrastructure` capability spec，让后续业务 change 能引用契约
- 隔离重复 / 过时代码到 `docs/legacy/` 而非 `git rm`，留作历史参考
- 让 `docker compose up -d` 在贾维斯（Linux）和本地 Mac 上都能用同一份 compose 文件启动（仅环境变量切换）

**Non-Goals**:
- 不引入任何新业务逻辑（classification / detection / alerts 由后续三个 change 负责）
- 不改采集器、存储层、API 的业务行为（已脏改的合理优化保留即可）
- 不解决 recovery 服务的半成品状态（`main.py` 已禁用，留待后续 change 处理）
- 不写新单元测试（cleanup 不引入新行为，也无需测试新行为）
- 不部署到贾维斯（部署放在 SSH 隧道任务里单独做）
- 不改 ClickHouse schema（`config/clickhouse/init.sql` 维持原样）

## Decisions

### Decision 1：删除 VPN 网络块而非修复

**选**：把 `docker-vpn-gateway_vpn_network` 与 `crypto-data-network` 双网络配置删除，回退到默认 bridge 网络（compose 自动创建）；`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 环境变量也一起删。

**不选**：保留 VPN 块 + 让用户先装 docker-vpn-gateway 项目。

**理由**：
- 该项目本地不存在，且没有 README 说明从哪取
- 部署目标（贾维斯）本身在保加利亚出口 → 容器无需走代理
- 本地开发不连 Binance（业务代码不会触发出站请求） → 不需要代理
- 未来真要走代理，应该走 host network 或 docker compose `profiles` 条件加载，不应硬编码外部网络依赖
- 移除后 compose 文件可移植性大幅提升

### Decision 2：参数化卷路径而非硬编码

**选**：`${CLICKHOUSE_DATA_DIR:-/Volumes/FORGE/data/clickhouse}` —— 默认值面向本地 Mac，生产环境用 `.env` 覆盖。

**不选 A**：用 docker named volume（`clickhouse_data:`）。

**不选 B**：硬编码绝对路径。

**理由**：
- 用户明确选择"FORGE 外部独立目录"作为本地存储
- 贾维斯是 Linux，路径与 macOS 不同 → 必须可参数化
- named volume 把数据藏在 `/var/lib/docker/volumes/` 下，备份和直接访问不便（用户已表态需要数据外部可见）
- bind mount + env var 兼顾两边：本地默认值生效，生产覆盖即可

### Decision 3：移到 docs/legacy/ 而非 git rm

**选**：`src/enhanced_collectors/` 整目录、`src/utils/enhanced/` 整目录、`examples/enhanced_architecture_demo.py` → `docs/legacy/<original_path>/`。

**不选**：`git rm` 物理删除。

**理由**：
- 这些代码 8 个月前写时一定有思路（重试封装、配置管理等），未来 detection 或 alerts 模块设计时可能借鉴
- 移动后路径变更，自动化工具不会再误把它们当 src 包导入
- baseline-infrastructure spec 明文规定 `docs/legacy/` 内代码不可被 src/ 导入，可加 CI 检查
- git mv 保留 file history，比 rm + add 更友好

### Decision 4：cleanup 不创建数据目录 + 不写 .env

**选**：`/Volumes/FORGE/data/clickhouse` 目录的创建作为部署文档化步骤，不进 commit；`.env` 文件不进 commit（只维护 `.env.example`）。

**理由**：
- 数据目录跟开发者本地路径相关，commit 创建动作没意义
- `.env` 含潜在敏感信息（未来会有 `TELEGRAM_BOT_TOKEN`），必须 gitignore
- `.env.example` 提供完整 schema + 安全占位值，让接手的人能 `cp .env.example .env` 一键起步

### Decision 5：解 src/config/ 冲突 = 移文件 + 删空目录

**选**：把 `src/config/collectors.yml` 移到 `config/collectors.yml`（项目已有 `config/clickhouse/...`，统一规约），删除 `src/config/` 空目录。

**不选**：把 `src/config.py` 改名 `src/settings.py` 让出 namespace。

**理由**：
- `src/config.py` 已被多处 `from src.config import settings` 引用，改名牵连面大
- YAML 是数据，本来就该跟 SQL 配置一样放 `config/`，不属于 src 包
- 修复后 baseline-infrastructure spec 锁定该约定，未来不再犯

### Decision 6：cleanup 阻塞业务 change

**选**：本 change 必须 `archive` 进入主 specs 之后，才能开始任何业务 change 的 propose。

**理由**：
- 业务 change 的 spec 会 reference `baseline-infrastructure` 的 module wiring 约定
- baseline 没锁住，业务 change 的脚手架会建在流沙上

## Risks / Trade-offs

- **[Risk]** `docker-compose.yml` 改完后本地无法立即跑通验证（贾维斯还没部署 + 本地不准备跑采集器） → **Mitigation**：写 `docker compose config -q` 静态校验作为 cleanup 收尾任务的验证；真正运行验证延后到贾维斯部署时
- **[Risk]** 移动 `src/enhanced_collectors/` 等若有任何隐藏依赖，可能让 `import src.main` 失败 → **Mitigation**：移动后立刻跑 `python -c "from src.main import app"` 烟雾测试，作为 tasks.md 的硬性 gate
- **[Risk]** `src/config/collectors.yml` 移到 `config/` 后，原本若有代码硬编码 `src/config/collectors.yml` 路径会断 → **Mitigation**：先 grep 确认无引用再移；若有则连同更新引用
- **[Risk]** `.gitignore` 加 `.claude/settings.local.json` 但该文件已被 git 跟踪？ → 已查：`git status` 显示该文件是 untracked，加 ignore 即可生效
- **[Trade-off]** 把所有清理 + OpenSpec scaffolding + cleanup change 四件套塞进同一个 commit，commit 体积偏大；接受这个一次性成本，换来后续 change 的干净起点
- **[Trade-off]** 不做"VPN 通过 profiles 条件加载"的更优雅方案，因为 baseline 当前阶段只需要"能跑"，profiles 的复杂度等到真有 VPN 需求时再加

## Migration Plan

1. **第一步：grep 引用核查** — 确认 `src/config/` 没被代码引用、`src/enhanced_collectors/` 没被任何活代码 import
2. **第二步：原地修改 + 移动** — 按 tasks.md 顺序执行，每完成一项打勾
3. **第三步：静态校验** — `docker compose config -q` + `python -c "from src.main import app"` 都通过
4. **第四步：commit + archive** — 一次 commit 提交所有改动 + `/opsx:archive cleanup-baseline-and-lock` 把 baseline-infrastructure spec 合入主 specs

**Rollback**: 若任何 gate 失败，`git restore .` 回到工作树脏状态（OpenSpec scaffolding 文件保留），分析后重新执行。最坏情况 `git reset --hard 3d0af78` 回到初始 commit（损失 8 个月脏改动里的有用部分，需重做）。

## Open Questions

- 后续业务 change 是否需要 `baseline-infrastructure` 之外的 cross-cutting spec（如 `observability` / `data-retention`）？暂列入待定，等业务 change brainstorming 时一起决定。
- 贾维斯首次部署是否要写一个 `scripts/deploy-jarvis.sh`？倾向延后到 SSH 隧道任务里再决定。
