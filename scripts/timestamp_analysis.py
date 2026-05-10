#!/usr/bin/env python3
"""
时间戳格式分析和标准化脚本
"""
from datetime import datetime, timezone
import sys

# 分析当前时间戳问题
current_time = datetime.now(timezone.utc)
current_ms = int(current_time.timestamp() * 1000)

print("=== 时间戳格式分析 ===")
print(f"当前UTC时间: {current_time}")
print(f"正确毫秒时间戳: {current_ms}")
print()

# 数据库中的异常时间戳
db_timestamp_ms = 1757792820000
db_datetime = datetime.fromtimestamp(db_timestamp_ms / 1000, timezone.utc)

print("=== 数据库时间戳问题 ===")
print(f"数据库时间戳: {db_timestamp_ms}")
print(f"转换后时间: {db_datetime}")
print(f"预期时间: 2025-09-13 19:47:00")
print()

# 测试正确的时间戳转换
test_time = datetime(2025, 9, 13, 19, 47, 0, tzinfo=timezone.utc)
correct_ms = int(test_time.timestamp() * 1000)

print("=== 正确转换方式 ===")
print(f"目标时间: {test_time}")
print(f"正确毫秒时间戳: {correct_ms}")
print(f"Freqtrade格式: [{correct_ms}, 115912.3, ...]")
print()

print("=== 修复建议 ===")
print("1. 数据库时间戳转换有时区问题")
print("2. 需要确保所有时间都是UTC")
print("3. WebSocket和REST API都要使用统一格式")
print("4. 建议清空数据库重新收集")