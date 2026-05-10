## 1. Pre-flight Checks

- [x] 1.1 Grep `src/` 树确认 `src/config/collectors.yml` 没被任何活代码 import 或硬编码路径引用
- [x] 1.2 Grep `src/` 树确认 `src/enhanced_collectors`、`src/utils.enhanced` 没被任何活代码 import（结论：仅 enhanced_collectors 内部互引）
- [x] 1.3 Grep `src/` 树确认 `src/validators` 没被任何活代码 import（结论：仅被 enhanced_collectors 引用，可一起隔离）
- [x] 1.4 列出当前 `.claude/` 下所有文件，确认 `settings.local.json` 是用户私有、其余 OpenSpec scaffolding 文件可入库

## 2. Fix docker-compose.yml

- [x] 2.1 删除 `clickhouse` 服务下 `networks:` 块中的 `docker-vpn-gateway_vpn_network` 引用（连同 `crypto-data-network` 一起删，回到 compose 默认网络）
- [x] 2.2 删除 `data-service` 服务下 `networks:` 块中的 `docker-vpn-gateway_vpn_network` 引用
- [x] 2.3 删除 `data-service` 服务环境变量里的 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` 三行
- [x] 2.4 删除文件底部 `networks:` 段中 `docker-vpn-gateway_vpn_network: external: true` 整块（连同 networks 段全删）
- [x] 2.5 把 ClickHouse 数据卷路径改为 `${CLICKHOUSE_DATA_DIR:-/Volumes/FORGE/data/clickhouse}:/var/lib/clickhouse`
- [x] 2.6 把 data-service 日志卷改回相对路径 `./logs:/app/logs`
- [x] 2.7 删除顶部 `version: '3.8'` 行（compose v2+ 不再需要，现写法会产生 warning）
- [x] 2.8 跑 `docker compose config -q` 校验 compose 文件语法正确，且不再报缺失外部网络（exit=0 通过）

**额外参数化（spec Requirement 3 顺带覆盖）**：`platform`、`CLICKHOUSE_DATABASE/USER/PASSWORD`、`RECOVERY_CHECK_INTERVAL`、`MAX_GAP_MINUTES`、`TZ` 全部改为 `${VAR:-默认}` 形式，方便贾维斯 x86_64 部署时仅靠 `.env` 切换。

## 3. Resolve src/config Module Collision

- [x] 3.1 把两份 legacy collectors.yml（`src/config/collectors.yml` + `config/enhanced/collectors.yml`）一起移到 `docs/legacy/configs/`（schema 不同、都跟 enhanced_collectors 死代码绑定，不适合放主 config/）
- [x] 3.2 删除 `src/config/` + `config/enhanced/` 空目录
- [x] 3.3 跑 `PYTHONPATH=. python3 -c "from src.config import settings"`，错误只剩 `pydantic_settings` 缺失（env 未装），证明命名冲突已解 → 完整烟雾测试延后到 group 8.2

## 4. Add .gitignore

- [x] 4.1 `.gitignore` 已存在且覆盖大多数项；只补充缺失的 `.claude/settings.local.json` 一行（其余 logs/data/.env/__pycache__/.DS_Store 等已有）
- [x] 4.2 `git check-ignore -v` 抽样验证 4 个样本（.env、logs/test.log、__pycache__/x.pyc、.claude/settings.local.json）全部命中 ignore 规则

## 5. Quarantine Legacy Code

- [x] 5.1 创建 `docs/legacy/` 目录
- [x] 5.2 mv `src/enhanced_collectors/` → `docs/legacy/src_enhanced_collectors/`（git mv 失败因为是 untracked，等价 mv）
- [x] 5.3 mv `src/utils/enhanced/` → `docs/legacy/src_utils_enhanced/`
- [x] 5.4 mv `examples/enhanced_architecture_demo.py` → `docs/legacy/examples_enhanced_architecture_demo.py`，删空 `examples/` 目录
- [x] 5.5 写 `docs/legacy/README.md`：解释隔离原因、与 reference 区别、何时可删、运行时禁止 import

## 6. Quarantine Reference Code

- [x] 6.1 创建 `docs/reference/` 目录
- [x] 6.2 mv `src/validators/crypto_data_validator.py` → `docs/reference/qlib_style_data_validator.py`
- [x] 6.3 删除 `src/validators/__init__.py` + `src/validators/` 空目录
- [x] 6.4 写 `docs/reference/README.md`：解释参考用途、与 legacy 区别、detection 设计可借鉴

## 7. Add .env.example

- [x] 7.1 `.env.example` 已存在，直接重写更结构化的版本
- [x] 7.2 已对照 `src/config.py` 27 个 env var，全部覆盖；并补齐 `RECOVERY_SYMBOLS_PER_CYCLE`
- [x] 7.3 顶部加 Usage / Note / Legend 段；每个变量标 `# REQUIRED` 或 `# OPTIONAL`
- [x] 7.4 加 `CLICKHOUSE_DATA_DIR`（cleanup 引入）+ `DOCKER_PLATFORM` + `TZ`；底部预留 `add-volume-*` change 将引入的 placeholder 段

## 8. Static Validation Gates

- [x] 8.1 `docker compose config -q` exit 0，无 error / warning
- [x] 8.2 `python3 -m compileall -q src/` exit 0（替代方案：完整 import 测试需要 conda env，环境装好后另跑 `python -c "from src.main import app"`）
- [x] 8.3 `git status` 干净 — 所有改动属于本 change 预期范围（修改文件、未跟踪 docs/legacy / docs/reference / openspec/changes/...）
- [x] 8.4 `grep -RIn "from docs.legacy\|import docs.legacy" src/` 0 匹配
- [x] 8.5 重写 collision 检查（前一版误把 `__init__`/`__pycache__` 算冲突）：实际遍历 src 各层级、对每个 dir 看是否存在同名 `.py` 文件 → 0 冲突

## 9. Commit + Archive

- [x] 9.1 `git add -A` 所有 staged 改动（55 个文件 +11322 -261）
- [x] 9.2 HEREDOC commit `326f9cb`，含 cleanup 完整动作清单 + 保留改动说明 + Co-Authored-By
- [x] 9.3 `openspec validate cleanup-baseline-and-lock --strict` PASS
- [x] 9.4 `openspec archive cleanup-baseline-and-lock -y` 把 baseline-infrastructure spec 合入 `openspec/specs/`，change 移到 `openspec/changes/archive/`
- [x] 9.5 二次 commit 包含归档动作产生的文件移动
- [x] 9.6 `git status` 确认工作树彻底干净

**额外清理（pre-commit 发现）**：`tests/test_enhanced_architecture.py` 与 `tests/unit/test_*.py` 五个文件都是测 docs/legacy 已隔离代码的，一起送到 `docs/legacy/tests/` 避免污染 `pytest` 测试信号。
