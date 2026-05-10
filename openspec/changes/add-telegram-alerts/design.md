## Context

本 change 是成交量异常监控系统的最后一块拼图：把 detection 的事件输出推到用户能看到的 Telegram 单聊。这是首个产生"用户可见外部副作用"的 change，对正确性的要求最高（错的告警 / 漏的告警 / 刷屏 都直接打扰用户）。

去重策略 / 限流策略 / 消息格式的"为什么"已经在 `docs/superpowers/specs/2026-05-10-volume-anomaly-monitor-design.md` 第 3 节 Alerts 部分论证。本 design.md 覆盖**模块内工程决策 + 端到端集成测试设计**。

## Goals / Non-Goals

**Goals**：
- 实现 detection 的 `AlertPublisher` Protocol，**0 行修改 detection 模块代码**就能接入（验证之前 design 的解耦正确）
- 默认安全：缺配置自动 dry-run、`ALERTS_DRY_RUN=true` 一键关推送、不加配置只落库可观察
- 失败兜底：Telegram 挂不影响 detection / 落库；任何告警都先落库再尝试推
- 端到端集成测试覆盖整个 pipeline，确保 3 个 change 真正可组合

**Non-Goals**：
- 不实现多渠道（Discord / 邮件等），未来需要时通过加新 publisher 实现 `AlertPublisher` 即可
- 不实现告警聚合摘要、不做 daily summary
- 不做"告警接收人路由"（单 chat_id 全收）
- 不实现持久化重试队列（接受失败即记录、不无限重试）
- 不做"告警内嵌图表"（Telegram 支持 photo, 但生成图表是另一坨依赖，未来 enhancement）

## Decisions

### Decision 1：Telegram client 选 httpx 直接调 vs python-telegram-bot 库

**选**：**httpx 直接调 Telegram Bot API HTTP endpoints**，自己写一个薄包装类（~100 行）

**不选**：`python-telegram-bot==21.x` 完整库

**理由**：
- 我们只用 sendMessage 这一个 API，python-telegram-bot 的 90% 功能（updates / handlers / polling）用不到
- python-telegram-bot 引入 ~10MB 依赖、~30 个间接依赖，增加 supply chain 风险
- httpx 已经在 baseline 里间接依赖（aiochclient 用），加直接依赖几乎零成本
- 自己 wrap 后单测更容易（直接 mock httpx.AsyncClient）

### Decision 2：`Notifier.publish()` 的并发模型

**选**：`asyncio.gather(*[send_one(e) for e in events], return_exceptions=True)` 并发发，每个 send 内部串行 retry。

**不选 A**：完全串行（events 多时太慢）
**不选 B**：用 `asyncio.Semaphore(N)` 限并发（Telegram 自然限 ~30 msg/sec，gather 全并发不会真打爆）
**不选 C**：用 ThreadPoolExecutor

**理由**：
- 单次 cycle 的 events 数量通常 <20（"进档+升级"过滤后），偶尔大事件 100 条 gather 也撑得住
- Telegram 服务端会自然 429 限流，retry 逻辑会消化
- gather + return_exceptions 让一条失败不影响其他

### Decision 3：volume_alerts 表的写入时序

**选**：先 batch insert all events（status=pending）→ 然后并发 send → 最后 batch update status

**不选 A**：每个 event 独立 insert + send + update（3 × N 次 ClickHouse 操作）
**不选 B**：先 send 再 insert 最终结果（缓存 N 个事件期间崩溃则丢失审计）

**理由**：
- 选 A 性能差且事务边界混乱
- 选 B 违反"先记录后行动"原则，崩溃恢复差
- 当前选项保证：任何 event 都至少写一次库，状态最终更新
- ClickHouse 不支持原子 UPDATE，但 ALTER TABLE ... UPDATE 是异步的；可以接受短暂"pending"状态停留

### Decision 4：volume_alerts 的 ALTER UPDATE vs 直接 INSERT 新版本

**选**：用 `ALTER TABLE volume_alerts UPDATE telegram_status=..., sent_at=... WHERE detected_at=... AND symbol=...`

**不选**：用 ReplacingMergeTree 写新版本

**理由**：
- 写新版本会让审计表行数翻倍
- ALTER UPDATE 在 ClickHouse 是 mutation（异步、可能延迟），但对告警审计不需要"实时一致"
- 状态字段更新是低频操作（5min 一波 events），不会触发频繁 mutation 导致性能问题

### Decision 5：消息格式 Markdown vs HTML vs MarkdownV2

**选**：**Markdown**（Telegram 称为 "legacy Markdown"）

**不选 A**：MarkdownV2（更严格但要 escape 大量特殊字符）
**不选 B**：HTML

**理由**：
- 我们的消息内容是固定格式 + 数字 + symbol 名（如 `BTC/USDT`），转义需求有限
- 老 Markdown 对 `_` `*` 等的转义规则更宽松，写起来短
- HTML 在手机字体上不如 Markdown 紧凑

### Decision 6：错误处理边界

**选**：Notifier 内所有错误 catch 后转化为"事件状态更新"，**绝不向上 raise**。detection 模块拿到的 `publish()` 永远成功返回。

**不选**：让 publish 偶尔 raise，让 detection 决定怎么处理

**理由**：
- detection 不应该知道 alerts 怎么失败的（违反解耦）
- alerts 失败的代价是"消息发不出去"，但已经落库 → 用户事后从表里查
- detection 拿到 raise 反而可能把整轮 cycle 标 failed，反而影响 detection health（不合理）

### Decision 7：Notifier 的依赖注入位置

**选**：在 `src/main.py` lifespan 里实例化 Notifier，**替换** detection change 注入的 `LoggingPublisher`

**不选**：在 alerts/__init__.py 里全局单例

**理由**：
- 全局单例难做单元测试和 dry-run 切换
- lifespan 注入符合 baseline-infrastructure 的 Module Wiring Convention
- Notifier 持有 ClickHouse client + Telegram client + dry_run 状态，需要 startup 时配置

### Decision 8：端到端集成测试的范围

**选**：testcontainers ClickHouse + 自写的 Telegram fake HTTP server（aiohttp 起一个 endpoint 接收 sendMessage 请求并返回 200）

**不选 A**：用 mock 库 mock httpx
**不选 B**：用真 Telegram bot 发到测试 chat

**理由**：
- A 测不到 HTTP 行为（连接、重试、429）
- B 不能在 CI 跑（需要真 token + 真 chat）
- 自写 fake 简单（30 行），且能 assert 收到的 request body / sequence

## Risks / Trade-offs

- **[Risk]** Notifier 启动时检测不到 TELEGRAM_BOT_TOKEN 自动 dry-run，operator 可能没注意到一直在 dry-run 模式 → **Mitigation**：startup log 用 WARNING 级别明确写"DRY RUN ENABLED, no Telegram messages will be sent"；`/health` 标 degraded
- **[Risk]** ClickHouse `ALTER UPDATE` mutation 是异步的，pending 状态可能停留几秒到几十秒 → **Mitigation**：可接受。`/health.alerts.last_send_at` 反映的是"最后一次成功发出"而非"最后一次状态确认"
- **[Risk]** 一次大事件触发 200 条 events，并发 200 个 Telegram 请求可能击中 Telegram 全局限流 → **Mitigation**：retry 逻辑消化 429；如果连续大事件让 retry 也无法消化，operator 可临时设 `ALERTS_DRY_RUN=true`
- **[Risk]** 端到端测试在 CI 环境跑 testcontainers 可能慢（启 ClickHouse 容器 ~30s）→ **Mitigation**：标 `@pytest.mark.integration`，本地开发跑 `pytest tests/unit`，CI 才跑全套
- **[Trade-off]** 自写 Telegram client 而不用现成库 → 接受 100 行额外代码 换 supply chain 干净
- **[Trade-off]** Telegram fake 用 aiohttp 而不是 mock httpx → 多了一个 test-only 依赖（aiohttp），但测得更真

## Migration Plan

1. 加 `httpx` 到依赖（如果还没显式列）
2. 加 `volume_alerts` 表 DDL 到 `config/clickhouse/init.sql`
3. 实现 `src/alerts/` 模块（TDD：formatter → telegram_client → notifier → api）
4. 修改 `src/main.py`：lifespan 替换 publisher 注入
5. 修改 `/health` 聚合 alerts 子健康
6. 跑端到端集成测试
7. 默认 `ALERTS_DRY_RUN=true` 部署到贾维斯，观察 24h 看落库正确
8. 配 `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`，关 dry-run，开始真发
9. archive change

**Rollback**：`git revert` + 设 `ALERTS_DRY_RUN=true` 立即停止外部消息。`volume_alerts` 表保留无害。lifespan 恢复注入 `LoggingPublisher`。

## Open Questions

- 是否要加 "deduplication safety net"，例如同 symbol 同 level 1 小时内重复事件直接跳过（防御 detection bug 导致刷屏）？倾向**不加**，detection 已经做去重，多一层重复逻辑增加调试难度。如果未来真出 bug 再加
- 消息里要不要加 TradingView 跳转链接（如 `https://www.tradingview.com/symbols/BTCUSDT.P/`）？倾向**未来 enhancement**，不在本 change 范围
- 是否暴露 "重发失败的告警" admin endpoint？倾向**不实现**，运维可以直接 SQL 查 + 手动调 Telegram API
