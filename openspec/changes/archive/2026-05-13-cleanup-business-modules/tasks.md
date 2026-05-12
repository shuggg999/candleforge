# Tasks: cleanup-business-modules

## 1. 删除业务模块代码
- [x] 1.1 `git rm -r src/classification/`
- [x] 1.2 `git rm -r src/detection/`
- [x] 1.3 `git rm -r src/alerts/`
- [x] 1.4 `git rm src/scheduler.py`

## 2. 改 src/main.py
- [x] 2.1 删除 9 处 `from src.{classification,detection,alerts,scheduler} import ...`
- [x] 2.2 删除 lifespan startup 中 `Classifier(...)` / `Detector(...)` / `Notifier(...)` / `TelegramClient(...)` 实例化
- [x] 2.3 删除 lifespan startup 中 `scheduler.start()` + shutdown 中 `scheduler.shutdown()`
- [x] 2.4 删除 3 处 `app.include_router(classification_router/detection_router/alerts_router, prefix="/api/v1")`
- [x] 2.5 删除 health sub-probe 函数中 `classification` / `detection` / `alerts` 三个键
- [x] 2.6 删除 `set_classification_singleton` / `set_detection_singleton` / `set_alerts_singleton` 调用
- [x] 2.7 `python -c "import src.main"` 不报 ModuleNotFoundError

## 3. 改 src/config.py
- [x] 3.1 删除 `CLASSIFICATION_REFRESH_HOURS` 字段
- [x] 3.2 删除 `DETECTION_INTERVAL_MINUTES` / `DETECTION_THRESHOLDS` / `DETECTION_BASELINE_HOURS` / `DETECTION_CURRENT_MINUTES` / `DETECTION_MIN_SAMPLES` 字段
- [x] 3.3 删除 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` / `TELEGRAM_PARSE_MODE` / `TELEGRAM_PROXY_URL` 字段
- [x] 3.4 删除 `BACKFILL_BATCH_RPS` 字段（仅 detection backfill 用）
- [x] 3.5 `python -c "from src.config import get_settings; get_settings()"` 不报 ValidationError

## 4. 改 .env.example
- [x] 4.1 删除 `# --- Telegram Alerts ---` 整段
- [x] 4.2 删除任何 `DETECTION_*` / `CLASSIFICATION_*` / `ALERTS_*` 行
- [x] 4.3 加注释指向 `volume-monitor/.env.example` 和 `telegram-bot/.env.example`

## 5. 删除业务模块测试
- [x] 5.1 `git rm tests/unit/test_notifier.py`
- [x] 5.2 `git rm tests/unit/test_detector.py`
- [x] 5.3 `git rm tests/unit/test_telegram_client.py`
- [x] 5.4 `git rm tests/unit/test_detection_api.py`
- [x] 5.5 `git rm tests/unit/test_scheduler.py`
- [x] 5.6 `git rm tests/unit/test_formatter.py`
- [x] 5.7 `git rm tests/unit/test_backfill.py`
- [x] 5.8 `git rm tests/unit/test_classifier.py`
- [x] 5.9 `git rm tests/integration/test_classification_e2e.py`
- [x] 5.10 `git rm tests/integration/test_detection_e2e.py`
- [x] 5.11 `git rm tests/integration/test_full_pipeline.py`
- [x] 5.12 改 `tests/unit/test_health_endpoint.py` 删除 classification/detection/alerts sub-probe 相关断言

## 6. 验证（本地）
- [x] 6.1 `pytest tests/ -x -q` 全绿（剩余 4 个测试文件: nats_publisher / clickhouse_nats_integration / health_endpoint / ttl_init_sql）
- [x] 6.2 `python -c "import src.main"` import 无错误
- [x] 6.3 `grep -rE "from src\.(alerts|classification|detection|scheduler)" src/ tests/` 零匹配

## 7. 部署到 jarvis
- [x] 7.1 git commit + push 到 gitea
- [x] 7.2 ssh jarvis 拉新代码 + `docker compose build data-service`
- [x] 7.3 `docker compose up -d data-service` 重启
- [x] 7.4 验证 5 容器全 `Up (healthy)`
- [x] 7.5 `curl jarvis:8100/api/v1/health | jq '.details | keys'` 应只有 `["database","nats","recovery","ws_collectors"]`
- [x] 7.6 `curl jarvis:8100/api/v1/tiers` 返回 404
- [x] 7.7 `curl jarvis:8100/api/v1/detection/recent` 返回 404
- [x] 7.8 `curl jarvis:8100/api/v1/alerts/audit` 返回 404
- [x] 7.9 验证 NATS publish 仍正常：`docker exec nats nats stream info OHLCV` 显示消息数继续增长
- [x] 7.10 e2e 烟雾测试：volume-monitor → telegram-bot 链路 POST /alerts 仍能成功推送

## 8. 镜像清理
- [x] 8.1 `docker rmi docker.1ms.run/library/nats:2.10-alpine docker.1ms.run/mambaorg/micromamba:1.5 docker.1ms.run/natsio/nats-box:latest natsio/nats-box:latest continuumio/miniconda3:latest`
- [x] 8.2 `docker image prune -f` 清 dangling
- [x] 8.3 验证 `docker images` 输出 size 收缩

## 9. Archive
- [x] 9.1 `openspec validate cleanup-business-modules --strict` 通过
- [x] 9.2 `openspec archive cleanup-business-modules`
- [x] 9.3 确认 `openspec/specs/{volume-classification,volume-detection,telegram-alerts}/` 目录已被 archive 删除
- [x] 9.4 确认 `openspec/specs/baseline-infrastructure/spec.md` health sub-probe 列表已更新
