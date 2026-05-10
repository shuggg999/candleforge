#!/usr/bin/env python3
"""
修复时间戳格式并清空重启数据收集
"""
import asyncio
import sys
import os
sys.path.append('/app')

from src.storage.clickhouse import ClickHouseManager
from src.config import settings
from datetime import datetime, timezone

async def fix_database_schema():
    """修复数据库结构并清空数据"""
    print("🔧 开始修复数据库格式...")

    db_manager = ClickHouseManager()
    await db_manager.initialize()

    try:
        # 1. 备份表结构
        print("📋 备份当前表结构...")
        backup_sql = """
        CREATE TABLE IF NOT EXISTS crypto_data.ohlcv_futures_backup AS
        SELECT * FROM crypto_data.ohlcv_futures LIMIT 0
        """
        await db_manager.client.execute(backup_sql)

        # 2. 清空现有数据
        print("🗑️ 清空现有数据...")
        truncate_sql = "TRUNCATE TABLE crypto_data.ohlcv_futures"
        await db_manager.client.execute(truncate_sql)

        # 3. 重置自增ID表（如果有）
        print("🔄 重置相关表...")
        reset_tables = [
            "TRUNCATE TABLE IF EXISTS crypto_data.collector_status",
            "TRUNCATE TABLE IF EXISTS crypto_data.data_gaps",
            "TRUNCATE TABLE IF EXISTS crypto_data.system_stats"
        ]

        for sql in reset_tables:
            try:
                await db_manager.client.execute(sql)
            except Exception as e:
                print(f"⚠️ 重置表时出错（可忽略）: {e}")

        # 4. 创建新的Freqtrade兼容视图
        print("📊 创建Freqtrade兼容视图...")
        view_sql = """
        CREATE OR REPLACE VIEW crypto_data.freqtrade_ohlcv AS
        SELECT
            exchange,
            symbol as pair,
            timeframe,
            toUnixTimestamp(timestamp) * 1000 as timestamp_ms,
            toFloat64(open) as open,
            toFloat64(high) as high,
            toFloat64(low) as low,
            toFloat64(close) as close,
            toFloat64(volume) as volume
        FROM crypto_data.ohlcv_futures
        ORDER BY timestamp ASC
        """
        await db_manager.client.execute(view_sql)

        print("✅ 数据库修复完成！")
        print()
        print("📊 数据统计:")

        # 检查清空结果
        count_sql = "SELECT COUNT(*) FROM crypto_data.ohlcv_futures"
        result = await db_manager.client.fetchval(count_sql)
        print(f"   - 主表记录数: {result}")

        return True

    except Exception as e:
        print(f"❌ 修复过程出错: {e}")
        return False
    finally:
        await db_manager.close()

def restart_data_service():
    """重启数据收集服务"""
    print("🚀 重启数据收集服务...")
    print("请手动执行以下命令:")
    print("docker-compose restart data-service")
    print()
    print("或者在容器内重启应用:")
    print("docker exec data-service pkill -f 'python -m src.main'")

async def main():
    print("=" * 50)
    print("🔧 Freqtrade数据服务修复工具")
    print("=" * 50)
    print()
    print("此工具将:")
    print("1. 清空所有现有数据")
    print("2. 重置收集器状态")
    print("3. 创建Freqtrade兼容视图")
    print("4. 重启数据收集")
    print()

    confirm = input("确认继续？这将删除所有现有数据 (y/N): ")
    if confirm.lower() != 'y':
        print("❌ 操作已取消")
        return

    # 修复数据库
    success = await fix_database_schema()
    if not success:
        print("❌ 修复失败，请检查错误信息")
        return

    # 提示重启服务
    restart_data_service()

    print("✅ 修复完成！")
    print()
    print("📝 后续步骤:")
    print("1. 重启data-service容器")
    print("2. 等待5-10分钟收集新数据")
    print("3. 验证数据格式正确性")
    print("4. 测试Freqtrade集成")

if __name__ == "__main__":
    asyncio.run(main())