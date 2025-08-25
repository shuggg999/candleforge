# 📊 Freqtrade Data Service

> 独立的加密货币合约数据服务，专为Freqtrade多级别策略提供高质量K线数据

## 🎯 项目简介

本项目是一个专门为解决Freqtrade策略在获取大量K线数据时遇到的API限制问题而设计的独立数据服务。通过WebSocket实时采集和ClickHouse高性能存储，为策略提供稳定、快速的数据访问。

### 核心特性
- 🚀 **WebSocket实时采集** - 零API权重消耗，支持475个USDT永续合约
- 📈 **合约交易特化** - 专注USDT永续合约，支持持仓量、资金费率等数据
- 🔄 **多级别联动查询** - 一次请求获取多个时间框架，优化策略性能
- 💾 **智能存储管理** - 自动TTL清理，可扩展至1.8TB外部存储
- 🛡️ **数据可靠性** - 自动检测补充缺失数据，零重复数据保证
- 🔄 **企业级监控** - 自动故障恢复、日志轮转、健康监控
- 🐳 **Docker一键部署** - 简化运维，开箱即用

### 支持交易所
- Binance (主要)
- OKX
- Bybit

### 系统要求
- Docker & Docker Compose
- 8GB+ 内存
- 10GB+ 磁盘空间
- 稳定网络连接

## 🚀 快速开始

### 1. 克隆项目
```bash
git clone https://github.com/your-username/freqtrade-data-service.git
cd freqtrade-data-service
```

### 2. 配置环境
```bash
cp .env.example .env
# 编辑.env文件，配置必要的参数
```

### 3. 启动服务
```bash
docker-compose up -d
```

### 4. 验证服务
```bash
# 健康检查
curl http://localhost:8000/api/v1/health

# 获取数据
curl http://localhost:8000/api/v1/ohlcv/binance/BTCUSDT/5m?limit=100
```

## 📚 文档

- [项目开发计划](PROJECT_PLAN.md) - 详细的技术方案和开发指南
- [API文档](docs/API.md) - 接口说明和使用示例
- [部署指南](docs/DEPLOYMENT.md) - 生产环境部署说明

## 🏗️ 架构概览

```
Freqtrade Strategy → FastAPI Service → ClickHouse Database
                           ↑
                    WebSocket Collectors
                           ↑
                    Exchange APIs (Binance/OKX/Bybit)
```

## 📊 API示例

### 获取K线数据
```python
import requests

# 单一时间框架
response = requests.get(
    "http://localhost:8000/api/v1/ohlcv/binance/BTCUSDT/5m",
    params={"limit": 1000}
)
data = response.json()

# 多级别查询（策略优化）
response = requests.get(
    "http://localhost:8000/api/v1/multi-timeframe/BTCUSDT"
)
all_timeframes = response.json()
```

## 🔧 配置说明

主要配置项在 `.env` 文件中：

```bash
# 数据库配置
CLICKHOUSE_HOST=localhost
CLICKHOUSE_PORT=9000

# 服务配置
API_PORT=8000
LOG_LEVEL=INFO

# 数据保留策略（天）
TTL_1M=7
TTL_5M=30
TTL_15M=90
TTL_1H=365
```

## 📈 性能指标

- **数据延迟**: <100ms (WebSocket实时采集)
- **查询响应**: 21ms (ClickHouse平均批处理时间)
- **处理能力**: 1,969条记录/秒
- **并发支持**: 475个合约 × 5个WebSocket连接
- **存储空间**: 支持1.8TB外部存储扩展
- **系统稳定性**: 企业级自动恢复，零错误运行

## 🔧 新增监控功能

### 日志管理API
```bash
# 获取日志统计
curl http://localhost:8000/api/v1/admin/logs/stats

# 手动触发日志轮转
curl -X POST http://localhost:8000/api/v1/admin/logs/rotate
```

### 自动恢复监控API
```bash
# 查看系统健康状态
curl http://localhost:8000/api/v1/admin/system/health

# 查看自动恢复状态
curl http://localhost:8000/api/v1/admin/recovery/status

# 强制恢复特定组件
curl -X POST http://localhost:8000/api/v1/admin/recovery/force/clickhouse_db
```

## 🤝 贡献

欢迎提交Issue和Pull Request！

## 📝 许可证

MIT License

## 🙏 致谢

- [Freqtrade](https://www.freqtrade.io/) - 优秀的开源交易框架
- [CCXT](https://github.com/ccxt/ccxt) - 统一的交易所接口
- [ClickHouse](https://clickhouse.com/) - 高性能时序数据库

---

**注意**: 本项目仅供学习研究使用，不构成投资建议。加密货币交易具有高风险，请谨慎操作。