"""
Storage layer for the data service
"""
from .clickhouse import ClickHouseManager

__all__ = ['ClickHouseManager']