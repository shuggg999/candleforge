"""
数据格式化工具 - 统一数据输出格式
"""
from datetime import datetime
from typing import Dict, List, Any, Optional
import zoneinfo
from decimal import Decimal


class DataFormatter:
    """数据格式化器，统一输出格式"""
    
    def __init__(self):
        self.beijing_tz = zoneinfo.ZoneInfo("Asia/Shanghai")
    
    def format_beijing_time(self, dt: datetime, include_timezone: bool = True) -> str:
        """格式化为北京时间字符串"""
        if dt.tzinfo is None:
            # 假设是UTC时间
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        
        beijing_dt = dt.astimezone(self.beijing_tz)
        
        if include_timezone:
            return beijing_dt.strftime('%Y-%m-%d %H:%M:%S CST')
        else:
            return beijing_dt.strftime('%Y-%m-%d %H:%M:%S')
    
    def format_compact_time(self, dt: datetime) -> str:
        """紧凑时间格式: 2025-08-24T16:25:00+08:00"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        
        beijing_dt = dt.astimezone(self.beijing_tz)
        return beijing_dt.isoformat(timespec='seconds')
    
    def format_price(self, price: Any, decimals: int = 2) -> str:
        """格式化价格，保留指定小数位"""
        if price is None:
            return "0.00"
        
        if isinstance(price, (int, float, Decimal)):
            return f"{float(price):.{decimals}f}"
        
        return str(price)
    
    def format_volume(self, volume: Any, decimals: int = 4) -> str:
        """格式化交易量"""
        if volume is None:
            return "0.0000"
        
        if isinstance(volume, (int, float, Decimal)):
            return f"{float(volume):.{decimals}f}"
        
        return str(volume)
    
    def format_ohlcv_csv(self, data: Dict[str, Any]) -> str:
        """格式化为CSV格式的字符串"""
        timestamp_str = self.format_compact_time(data['timestamp'])
        
        return (f"{data['symbol']},{data['timeframe']},{timestamp_str},"
                f"{self.format_price(data['open'])},"
                f"{self.format_price(data['high'])},"
                f"{self.format_price(data['low'])},"
                f"{self.format_price(data['close'])},"
                f"{self.format_volume(data['volume'])}")
    
    def format_ohlcv_display(self, data: Dict[str, Any]) -> str:
        """格式化为显示友好的字符串"""
        timestamp_str = self.format_beijing_time(data['timestamp'])
        
        return (f"{data['symbol']} {data['timeframe']} | "
                f"{timestamp_str} | "
                f"O:{self.format_price(data['open'])} "
                f"H:{self.format_price(data['high'])} "
                f"L:{self.format_price(data['low'])} "
                f"C:{self.format_price(data['close'])} "
                f"V:{self.format_volume(data['volume'])}")
    
    def format_ohlcv_json(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """格式化为标准JSON格式"""
        return {
            'symbol': data['symbol'],
            'timeframe': data['timeframe'],
            'timestamp': self.format_compact_time(data['timestamp']),
            'beijing_time': self.format_beijing_time(data['timestamp']),
            'ohlcv': {
                'open': self.format_price(data['open']),
                'high': self.format_price(data['high']),
                'low': self.format_price(data['low']),
                'close': self.format_price(data['close']),
                'volume': self.format_volume(data['volume'])
            },
            'metadata': {
                'exchange': data.get('exchange'),
                'turnover': self.format_volume(data.get('turnover')),
                'trades_count': data.get('trades_count'),
                'data_quality': data.get('data_quality', 'raw')
            }
        }
    
    def format_batch_csv(self, data_list: List[Dict[str, Any]], 
                        include_header: bool = True) -> str:
        """批量格式化为CSV格式"""
        lines = []
        
        if include_header:
            lines.append("symbol,timeframe,timestamp,open,high,low,close,volume")
        
        for data in data_list:
            lines.append(self.format_ohlcv_csv(data))
        
        return '\n'.join(lines)
    
    def format_summary_stats(self, stats: Dict[str, Any]) -> str:
        """格式化统计摘要"""
        lines = []
        lines.append("=" * 60)
        lines.append("📊 数据服务统计报告")
        lines.append("=" * 60)
        lines.append(f"⏰ 报告时间: {self.format_beijing_time(datetime.now())}")
        lines.append("")
        
        if 'collectors' in stats:
            lines.append("📡 数据收集器状态:")
            collectors_data = stats['collectors']
            
            if isinstance(collectors_data, dict):
                for exchange, info in collectors_data.items():
                    if isinstance(info, dict):
                        status = info.get('running', False)
                        status_icon = "🟢" if status else "🔴"
                        lines.append(f"   {status_icon} {exchange}: {'运行中' if status else '已停止'}")
                        
                        if 'stats' in info:
                            s = info['stats']
                            lines.append(f"      消息接收: {s.get('messages_received', 0):,}")
                            lines.append(f"      数据存储: {s.get('data_points_stored', 0):,}")
                            if s.get('last_message_time'):
                                try:
                                    last_time = self.format_beijing_time(
                                        datetime.fromisoformat(s['last_message_time'].replace('Z', '+00:00'))
                                    )
                                    lines.append(f"      最后消息: {last_time}")
                                except:
                                    lines.append(f"      最后消息: {s['last_message_time']}")
        
        if 'database' in stats:
            lines.append("\n💾 数据库状态:")
            db_stats = stats['database']
            lines.append(f"   总记录数: {db_stats.get('total_records', 0):,}")
            if 'disk_usage' in db_stats:
                lines.append(f"   磁盘使用: {db_stats['disk_usage']}")
        
        return '\n'.join(lines)


# 全局格式化器实例
formatter = DataFormatter()