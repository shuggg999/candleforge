# `docs/legacy/` —— 隔离区

## 这里放什么

8 个月前（2025-08~09 期间）尝试搭建过一套"增强版数据采集架构"，包括：

- `src_enhanced_collectors/` — Binance / OKX / Bybit 三个交易所的"增强采集器"，配套一个 `collection_manager`
- `src_utils_enhanced/` — 自研的 `config_manager` / `retry_decorator` / `smart_concurrency_controller`
- `examples_enhanced_architecture_demo.py` — 上述架构的 demo
- `configs/src_config_collectors.yml`、`configs/config_enhanced_collectors.yml` — 两份不同 schema 的配置文件，对应增强采集器的两套半完成实现

这套代码与目前 `src/collectors/` 的实现**功能重叠、职责相同**，但完成度低、未在生产跑通过。

## 为什么不直接 `git rm`

1. **设计思路有借鉴价值**：当时考虑了重试退避、并发节流、配置热加载等问题，未来 detection 模块写 z-score 滚动窗口、alerts 模块写限流去重时，可以参考它的封装方式
2. **保留 git history**：用 `git mv` 而非 `git rm` + `git add`，blame 信息不丢
3. **作为反例**：是"不要再造同样的轮子"的具体物证 —— 已经引入 `tenacity` / `prometheus-client`，下次再想自研重试装饰器时来这里看一眼

## 运行时约束

- `src/` 下任何代码 **不得 import** 这里的任何模块（spec: `baseline-infrastructure` 第 9 条 Legacy Code Quarantine）
- 这里的代码 **可能跑不起来**（移动后 `from src.enhanced_collectors.x import Y` 这类 import 路径已断），不修
- 静态分析 / linter 应将本目录排除

## 何时可以彻底删除

满足任一条件即可 `git rm -r docs/legacy/`：

1. `add-volume-detection` change archive 之后，确认 detection 实现未借鉴任何 enhanced_* 思路
2. 仓库存在 6 个月以上、无人 reference 这里的代码、git log 显示零访问
3. 出于敏感信息或合规清理需求
