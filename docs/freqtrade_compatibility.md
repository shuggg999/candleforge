# Freqtrade 兼容性分析

## 当前数据库格式 vs Freqtrade需求

### ✅ 已满足的要求
1. **字段完整性** - 包含所有必需字段：timestamp, open, high, low, close, volume
2. **数据类型兼容** - Decimal可以转换为Float64
3. **排序支持** - 可以按timestamp排序
4. **时间戳转换** - DateTime64可以转换为毫秒时间戳

### ⚠️ 需要调整的差异

#### 1. **字段映射差异**
| Freqtrade需要 | 当前数据库字段 | 状态 |
|------------|------------|-----|
| `pair` | `symbol` | ✅ 字段名不同，内容匹配 |
| `timeframe` | `timeframe` | ✅ 完全匹配 |
| `timestamp` | `timestamp` | ⚠️ 需要转换为毫秒时间戳 |
| `open/high/low/close/volume` | `open/high/low/close/volume` | ⚠️ 需要类型转换 |

#### 2. **数据格式转换需求**
```sql
-- 当前格式
symbol: 'BTC/USDT' (String)
timestamp: '2025-09-13 19:43:00.000' (DateTime64)
open: 115943 (Decimal)

-- Freqtrade需要
[1640995200000, 47000.5, 47100.0, 46900.0, 47050.0, 1234.56]
```

#### 3. **时间戳转换问题**
⚠️ **发现重大问题**: 时间戳转换结果异常
```sql
-- 实际时间: 2025-09-13 19:43:00
-- 转换结果: 1757775720000 (对应 2025-09-13 但是时区可能有问题)
```

### 🔧 需要修改的地方

#### API层修改 (`src/api/routes.py`)
```python
# 需要添加 Freqtrade 兼容端点
@router.get("/freqtrade/{exchange}/{pair}/{timeframe}")
async def get_freqtrade_ohlcv():
    # 返回 List[List[数字]] 格式
    pass
```

#### 数据转换SQL
```sql
-- 正确的转换查询
SELECT [
    toUnixTimestamp(timestamp) * 1000,  -- 毫秒时间戳
    toFloat64(open),
    toFloat64(high),
    toFloat64(low),
    toFloat64(close),
    toFloat64(volume)
] as ohlcv_array
FROM crypto_data.ohlcv_futures
WHERE symbol = ? AND timeframe = ?
ORDER BY timestamp ASC;
```

#### 字段映射处理
```python
# symbol字段映射
freqtrade_pair = db_symbol.replace('/', '')  # BTC/USDT -> BTCUSDT
# 或者保持斜杠格式，看Freqtrade配置
```

### 📋 完整修改清单

1. **新建API端点** - 专门为Freqtrade提供数据
2. **时间戳转换修复** - 确保正确的毫秒级时间戳
3. **数据类型转换** - Decimal → Float64
4. **返回格式调整** - JSON数组 → 嵌套数组格式
5. **字段名称映射** - symbol → pair（如果需要）
6. **排序保证** - 必须按时间升序
7. **错误处理** - 缺失数据的处理逻辑

### 🎯 推荐实现方案

**选项1: 新建专用端点** (推荐)
- `/api/v1/freqtrade/{exchange}/{pair}/{timeframe}`
- 专门返回Freqtrade格式
- 不影响现有API

**选项2: 修改现有端点**
- 添加`format=freqtrade`参数
- 根据参数返回不同格式

**选项3: 直接ClickHouse集成**
- Freqtrade直接连接ClickHouse
- 在Freqtrade侧处理格式转换