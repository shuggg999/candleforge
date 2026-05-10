# 数据服务问题分析和解决方案

## 📊 发现的问题

### 1. ❌ 数据来源无法区分
**问题**: 所有数据都标记为`validated`，无法区分WebSocket和REST API来源
**原因**: 数据验证器覆盖了原始的`data_quality`字段
**解决方案**:
- WebSocket数据应标记为 `data_quality='websocket'`
- REST API数据应标记为 `data_quality='rest_api'`

### 2. ✅ 唯一性约束正常
**状态**: 没有重复数据
**主键**: `(exchange, symbol, timeframe, timestamp)`
**引擎**: `ReplacingMergeTree`自动去重

### 3. ✅ 时间戳对齐正确
**状态**: WebSocket和REST API时间戳已统一
- 1分钟K线：精确到分钟边界（0秒）
- 5分钟K线：精确到5分钟边界（0,5,10,15...）
- 使用`_normalize_timestamp()`函数确保对齐

### 4. ⚠️ 空字段问题
**open_interest（持仓量）**:
- 现货交易对没有持仓量
- 只有期货合约才有此数据
- Binance现货API不提供

**funding_rate（资金费率）**:
- 只适用于永续合约
- 现货交易没有资金费率
- 需要专门的API端点获取

### 5. 🔧 需要实现的改进

#### A. 数据源标识
```python
# WebSocket数据
data['data_quality'] = 'websocket'

# REST API数据
data['data_quality'] = 'rest_api'
```

#### B. 增加数据源字段
```sql
ALTER TABLE ohlcv_futures
ADD COLUMN data_source String DEFAULT 'unknown'
COMMENT 'Data source: websocket/rest_api/manual';
```

#### C. 期货数据支持
如果需要期货数据，应该：
1. 使用Binance Futures API而不是Spot API
2. 获取open_interest和funding_rate
3. 创建独立的期货数据表

## 📋 修复优先级

1. **高优先级**: 修复数据源标识
2. **中优先级**: 添加data_source字段追踪来源
3. **低优先级**: 期货特有字段（根据需求）

## 💡 关键洞察

- **现货 vs 期货**: 当前系统收集的是现货数据，不是期货
- **字段空值合理**: open_interest和funding_rate对现货来说本就该为空
- **时间戳统一成功**: WebSocket和REST API时间戳已正确对齐
- **去重机制有效**: ReplacingMergeTree确保数据唯一性