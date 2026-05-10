"""
专业加密货币数据验证器
借鉴Qlib的数据质量检查模式，提供全面的K线数据验证
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from loguru import logger
import time


@dataclass
class ValidationIssue:
    """验证问题"""
    row_index: int
    issue_type: str
    severity: str  # 'error', 'warning', 'info'
    details: str
    
    def __str__(self) -> str:
        return f"Row {self.row_index} [{self.severity.upper()}] {self.issue_type}: {self.details}"


@dataclass 
class ValidationResult:
    """验证结果"""
    is_valid: bool
    issues: List[ValidationIssue]
    fixed_data: Optional[pd.DataFrame]
    stats: Dict[str, Any]
    
    @property
    def error_count(self) -> int:
        return len([issue for issue in self.issues if issue.severity == 'error'])
    
    @property
    def warning_count(self) -> int:
        return len([issue for issue in self.issues if issue.severity == 'warning'])
    
    @property
    def has_errors(self) -> bool:
        return self.error_count > 0
    
    @property
    def has_warnings(self) -> bool:
        return self.warning_count > 0


class CryptoDataValidator:
    """
    专业加密货币数据验证器
    
    功能：
    1. OHLCV数据完整性验证
    2. 价格逻辑一致性检查
    3. 异常值检测和修复
    4. 时间序列连续性验证
    5. 自定义验证规则支持
    """
    
    def __init__(self, 
                 enable_outlier_detection: bool = True,
                 outlier_threshold: float = 3.0,
                 enable_ohlc_validation: bool = True, 
                 enable_volume_validation: bool = True,
                 enable_timestamp_validation: bool = True,
                 min_price: float = 0.0001,
                 max_price: float = 10000000.0,
                 min_volume: float = 0.0,
                 interpolation_method: str = 'linear',
                 auto_fix: bool = True):
        """
        初始化验证器
        
        Args:
            enable_outlier_detection: 启用异常值检测
            outlier_threshold: 异常值阈值（标准差倍数）
            enable_ohlc_validation: 启用OHLC逻辑验证
            enable_volume_validation: 启用成交量验证
            enable_timestamp_validation: 启用时间戳验证
            min_price: 最小有效价格
            max_price: 最大有效价格
            min_volume: 最小有效成交量
            interpolation_method: 插值方法
            auto_fix: 自动修复问题
        """
        self.enable_outlier_detection = enable_outlier_detection
        self.outlier_threshold = outlier_threshold
        self.enable_ohlc_validation = enable_ohlc_validation
        self.enable_volume_validation = enable_volume_validation
        self.enable_timestamp_validation = enable_timestamp_validation
        self.min_price = min_price
        self.max_price = max_price
        self.min_volume = min_volume
        self.interpolation_method = interpolation_method
        self.auto_fix = auto_fix
        
        # 价格列
        self.price_columns = ['open', 'high', 'low', 'close']
        # 必需列
        self.required_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'symbol', 'exchange']
        
        logger.info(
            f"CryptoDataValidator 初始化完成",
            extra={
                "outlier_detection": self.enable_outlier_detection,
                "outlier_threshold": self.outlier_threshold,
                "price_range": f"{self.min_price}-{self.max_price}",
                "auto_fix": self.auto_fix
            }
        )
    
    def validate_kline_data(self,
                          data: pd.DataFrame,
                          fix_issues: Optional[bool] = None,
                          detect_outliers: Optional[bool] = None,
                          check_timestamp_gaps: bool = False,
                          expected_interval: str = '1m',
                          outlier_threshold: Optional[float] = None,
                          custom_rules: Optional[List[Callable]] = None) -> ValidationResult:
        """
        验证K线数据
        
        Args:
            data: 待验证的数据
            fix_issues: 是否修复问题（None时使用初始化配置）
            detect_outliers: 是否检测异常值（None时使用初始化配置）
            check_timestamp_gaps: 检查时间戳缺口
            expected_interval: 期望的时间间隔
            outlier_threshold: 异常值阈值（None时使用初始化配置）
            custom_rules: 自定义验证规则
        """
        start_time = time.time()
        
        # 使用默认配置
        if fix_issues is None:
            fix_issues = self.auto_fix
        if detect_outliers is None:
            detect_outliers = self.enable_outlier_detection
        if outlier_threshold is None:
            outlier_threshold = self.outlier_threshold
            
        issues = []
        fixed_data = data.copy() if fix_issues else None
        
        # 1. 基本结构验证
        structure_issues = self._validate_structure(data)
        issues.extend(structure_issues)
        
        if len(structure_issues) > 0 and any(issue.severity == 'error' for issue in structure_issues):
            # 结构错误无法继续验证
            return self._create_result(False, issues, None, data, start_time)
        
        # 2. 缺失值检查
        missing_issues = self._validate_missing_values(data)
        issues.extend(missing_issues)
        
        if fix_issues and missing_issues:
            fixed_data = self._fix_missing_values(fixed_data)
        
        # 3. 数据类型和范围验证
        range_issues = self._validate_data_ranges(data)
        issues.extend(range_issues)
        
        if fix_issues and range_issues:
            fixed_data = self._fix_range_issues(fixed_data)
        
        # 4. OHLC逻辑验证
        if self.enable_ohlc_validation:
            ohlc_issues = self._validate_ohlc_consistency(data)
            issues.extend(ohlc_issues)
            
            if fix_issues and ohlc_issues:
                fixed_data = self._fix_ohlc_issues(fixed_data)
        
        # 5. 成交量验证
        if self.enable_volume_validation:
            volume_issues = self._validate_volume(data)
            issues.extend(volume_issues)
            
            if fix_issues and volume_issues:
                fixed_data = self._fix_volume_issues(fixed_data)
        
        # 6. 异常值检测
        if detect_outliers:
            outlier_issues = self._detect_outliers(data, outlier_threshold)
            issues.extend(outlier_issues)
            
            if fix_issues and outlier_issues:
                fixed_data = self._fix_outliers(fixed_data, outlier_threshold)
        
        # 7. 时间戳缺口检查
        if check_timestamp_gaps and self.enable_timestamp_validation:
            gap_issues = self._validate_timestamp_gaps(data, expected_interval)
            issues.extend(gap_issues)
        
        # 8. 自定义验证规则
        if custom_rules:
            custom_issues = self._apply_custom_rules(data, custom_rules)
            issues.extend(custom_issues)
        
        # 9. 清理无法修复的行
        if fix_issues and fixed_data is not None:
            fixed_data = self._remove_unfixable_rows(fixed_data)
        
        # 确定整体验证结果
        is_valid = len([issue for issue in issues if issue.severity == 'error']) == 0
        
        return self._create_result(is_valid, issues, fixed_data, data, start_time)
    
    def _validate_structure(self, data: pd.DataFrame) -> List[ValidationIssue]:
        """验证数据结构"""
        issues = []
        
        if data.empty:
            issues.append(ValidationIssue(
                row_index=-1,
                issue_type='empty_dataset',
                severity='error',
                details='Dataset is empty'
            ))
            return issues
        
        # 检查必需列
        missing_columns = set(self.required_columns) - set(data.columns)
        if missing_columns:
            issues.append(ValidationIssue(
                row_index=-1,
                issue_type='missing_columns',
                severity='error',
                details=f'Missing required columns: {missing_columns}'
            ))
        
        return issues
    
    def _validate_missing_values(self, data: pd.DataFrame) -> List[ValidationIssue]:
        """验证缺失值"""
        issues = []
        
        for column in self.required_columns:
            if column not in data.columns:
                continue
                
            missing_mask = data[column].isna()
            missing_indices = data[missing_mask].index.tolist()
            
            for idx in missing_indices:
                issues.append(ValidationIssue(
                    row_index=idx,
                    issue_type='missing_value',
                    severity='error',
                    details=f'Missing value in {column}'
                ))
        
        return issues
    
    def _validate_data_ranges(self, data: pd.DataFrame) -> List[ValidationIssue]:
        """验证数据范围"""
        issues = []
        
        # 检查价格列
        for column in self.price_columns:
            if column not in data.columns:
                continue
                
            # 负数检查
            negative_mask = data[column] < 0
            negative_indices = data[negative_mask].index.tolist()
            
            for idx in negative_indices:
                issues.append(ValidationIssue(
                    row_index=idx,
                    issue_type='negative_price',
                    severity='error',
                    details=f'{column} is negative: {data.loc[idx, column]}'
                ))
            
            # 无穷大检查
            inf_mask = np.isinf(data[column])
            inf_indices = data[inf_mask].index.tolist()
            
            for idx in inf_indices:
                issues.append(ValidationIssue(
                    row_index=idx,
                    issue_type='infinite_value',
                    severity='error',
                    details=f'{column} is infinite: {data.loc[idx, column]}'
                ))
            
            # 价格范围检查
            out_of_range_mask = (data[column] < self.min_price) | (data[column] > self.max_price)
            out_of_range_indices = data[out_of_range_mask].index.tolist()
            
            for idx in out_of_range_indices:
                issues.append(ValidationIssue(
                    row_index=idx,
                    issue_type='price_out_of_range',
                    severity='warning',
                    details=f'{column} out of range [{self.min_price}, {self.max_price}]: {data.loc[idx, column]}'
                ))
        
        return issues
    
    def _validate_ohlc_consistency(self, data: pd.DataFrame) -> List[ValidationIssue]:
        """验证OHLC价格逻辑一致性"""
        issues = []
        
        for idx, row in data.iterrows():
            try:
                open_price = float(row['open'])
                high_price = float(row['high'])
                low_price = float(row['low'])
                close_price = float(row['close'])
                
                # 检查 high >= max(open, close) and high >= low
                if high_price < max(open_price, close_price, low_price):
                    issues.append(ValidationIssue(
                        row_index=idx,
                        issue_type='ohlc_inconsistency',
                        severity='error',
                        details=f'High price {high_price} is not the highest among OHLC'
                    ))
                
                # 检查 low <= min(open, close) and low <= high
                if low_price > min(open_price, close_price, high_price):
                    issues.append(ValidationIssue(
                        row_index=idx,
                        issue_type='ohlc_inconsistency',
                        severity='error',
                        details=f'Low price {low_price} is not the lowest among OHLC'
                    ))
                
            except (ValueError, TypeError):
                # 数据类型错误已在其他验证中处理
                continue
        
        return issues
    
    def _validate_volume(self, data: pd.DataFrame) -> List[ValidationIssue]:
        """验证成交量"""
        issues = []
        
        if 'volume' not in data.columns:
            return issues
        
        # 负数或零成交量检查
        invalid_volume_mask = data['volume'] <= self.min_volume
        invalid_volume_indices = data[invalid_volume_mask].index.tolist()
        
        for idx in invalid_volume_indices:
            issues.append(ValidationIssue(
                row_index=idx,
                issue_type='invalid_volume',
                severity='error',
                details=f'Volume {data.loc[idx, "volume"]} is <= {self.min_volume}'
            ))
        
        # 无穷大成交量检查
        inf_volume_mask = np.isinf(data['volume'])
        inf_volume_indices = data[inf_volume_mask].index.tolist()
        
        for idx in inf_volume_indices:
            issues.append(ValidationIssue(
                row_index=idx,
                issue_type='infinite_value',
                severity='error',
                details=f'Volume is infinite: {data.loc[idx, "volume"]}'
            ))
        
        return issues
    
    def _detect_outliers(self, data: pd.DataFrame, threshold: float) -> List[ValidationIssue]:
        """检测异常值（使用改进的IQR+Z-score混合方法）"""
        issues = []
        
        for column in self.price_columns:
            if column not in data.columns:
                continue
            
            column_data = data[column].dropna()
            if len(column_data) < 3:
                continue  # 数据点太少，无法检测异常值
            
            # 方法1: IQR方法 (适用于小数据集)
            Q1 = column_data.quantile(0.25)
            Q3 = column_data.quantile(0.75)
            IQR = Q3 - Q1
            
            if IQR > 0:
                lower_bound = Q1 - 1.5 * IQR
                upper_bound = Q3 + 1.5 * IQR
                iqr_outliers = (column_data < lower_bound) | (column_data > upper_bound)
            else:
                iqr_outliers = pd.Series([False] * len(column_data), index=column_data.index)
            
            # 方法2: 改进的Z-score方法
            median_val = column_data.median()
            mad = np.median(np.abs(column_data - median_val))  # 中位数绝对偏差
            
            if mad > 0:
                # 使用修正的Z-score (更稳健)
                modified_z_scores = 0.6745 * (column_data - median_val) / mad
                zscore_outliers = np.abs(modified_z_scores) > threshold
            else:
                # 如果MAD为0，使用标准Z-score
                mean_val = column_data.mean()
                std_val = column_data.std()
                if std_val > 0:
                    z_scores = np.abs((column_data - mean_val) / std_val)
                    zscore_outliers = z_scores > threshold
                else:
                    zscore_outliers = pd.Series([False] * len(column_data), index=column_data.index)
            
            # 综合两种方法的结果
            combined_outliers = iqr_outliers | zscore_outliers
            outlier_indices = column_data[combined_outliers].index.tolist()
            
            for idx in outlier_indices:
                if mad > 0:
                    modified_z = 0.6745 * abs(column_data[idx] - median_val) / mad
                    details = f'{column} is outlier (modified z-score: {modified_z:.2f}): {data.loc[idx, column]}'
                else:
                    details = f'{column} is outlier (IQR method): {data.loc[idx, column]}'
                
                issues.append(ValidationIssue(
                    row_index=idx,
                    issue_type='price_outlier',
                    severity='warning',
                    details=details
                ))
        
        return issues
    
    def _validate_timestamp_gaps(self, data: pd.DataFrame, expected_interval: str) -> List[ValidationIssue]:
        """验证时间戳缺口"""
        issues = []
        
        if 'timestamp' not in data.columns or len(data) < 2:
            return issues
        
        # 解析时间间隔
        interval_seconds = self._parse_interval(expected_interval)
        if interval_seconds is None:
            return issues
        
        sorted_data = data.sort_values('timestamp')
        timestamps = sorted_data['timestamp'].values
        
        for i in range(1, len(timestamps)):
            prev_ts = pd.to_datetime(timestamps[i-1])
            curr_ts = pd.to_datetime(timestamps[i])
            
            expected_ts = prev_ts + timedelta(seconds=interval_seconds)
            time_diff = abs((curr_ts - expected_ts).total_seconds())
            
            # 允许小幅误差（5秒）
            if time_diff > 5:
                issues.append(ValidationIssue(
                    row_index=sorted_data.index[i],
                    issue_type='timestamp_gap',
                    severity='warning',
                    details=f'Timestamp gap detected. Expected: {expected_ts}, Got: {curr_ts}'
                ))
        
        return issues
    
    def _apply_custom_rules(self, data: pd.DataFrame, custom_rules: List[Callable]) -> List[ValidationIssue]:
        """应用自定义验证规则"""
        issues = []
        
        for rule_func in custom_rules:
            try:
                rule_issues = rule_func(data)
                for issue_dict in rule_issues:
                    issues.append(ValidationIssue(
                        row_index=issue_dict['row_index'],
                        issue_type=issue_dict['issue_type'],
                        severity=issue_dict.get('severity', 'warning'),
                        details=issue_dict['details']
                    ))
            except Exception as e:
                logger.error(f"Custom rule execution failed: {e}")
        
        return issues
    
    def _fix_missing_values(self, data: pd.DataFrame) -> pd.DataFrame:
        """修复缺失值"""
        fixed_data = data.copy()
        
        # 对价格列进行线性插值
        for column in self.price_columns:
            if column in fixed_data.columns:
                fixed_data[column] = fixed_data[column].interpolate(method=self.interpolation_method)
        
        # 对成交量进行前向填充
        if 'volume' in fixed_data.columns:
            fixed_data['volume'] = fixed_data['volume'].ffill()
            
        # 对字符串列进行前向填充
        for column in ['symbol', 'exchange']:
            if column in fixed_data.columns:
                fixed_data[column] = fixed_data[column].ffill()
        
        return fixed_data
    
    def _fix_range_issues(self, data: pd.DataFrame) -> pd.DataFrame:
        """修复范围问题"""
        fixed_data = data.copy()
        
        # 将负数价格设为NaN，然后插值
        for column in self.price_columns:
            if column in fixed_data.columns:
                fixed_data.loc[fixed_data[column] < 0, column] = np.nan
                fixed_data[column] = fixed_data[column].interpolate(method=self.interpolation_method)
        
        # 将无穷大值设为NaN，然后插值
        for column in self.price_columns + ['volume']:
            if column in fixed_data.columns:
                fixed_data.loc[np.isinf(fixed_data[column]), column] = np.nan
                fixed_data[column] = fixed_data[column].interpolate(method=self.interpolation_method)
        
        return fixed_data
    
    def _fix_ohlc_issues(self, data: pd.DataFrame) -> pd.DataFrame:
        """修复OHLC不一致问题"""
        fixed_data = data.copy()
        
        for idx, row in fixed_data.iterrows():
            try:
                open_price = float(row['open'])
                high_price = float(row['high'])
                low_price = float(row['low'])
                close_price = float(row['close'])
                
                # 修复high不是最高价的问题
                actual_high = max(open_price, high_price, low_price, close_price)
                if high_price < actual_high:
                    fixed_data.loc[idx, 'high'] = actual_high
                
                # 修复low不是最低价的问题
                actual_low = min(open_price, high_price, low_price, close_price)
                if low_price > actual_low:
                    fixed_data.loc[idx, 'low'] = actual_low
                
            except (ValueError, TypeError):
                continue
        
        return fixed_data
    
    def _fix_volume_issues(self, data: pd.DataFrame) -> pd.DataFrame:
        """修复成交量问题"""
        fixed_data = data.copy()
        
        if 'volume' in fixed_data.columns:
            # 将负数或零成交量设为NaN
            fixed_data.loc[fixed_data['volume'] <= self.min_volume, 'volume'] = np.nan
            # 用前一个有效值填充
            fixed_data['volume'] = fixed_data['volume'].ffill()
            # 如果还有NaN，用平均值填充
            if fixed_data['volume'].isna().any():
                mean_volume = fixed_data['volume'].mean()
                if not np.isnan(mean_volume):
                    fixed_data['volume'] = fixed_data['volume'].fillna(mean_volume)
        
        return fixed_data
    
    def _fix_outliers(self, data: pd.DataFrame, threshold: float) -> pd.DataFrame:
        """修复异常值"""
        fixed_data = data.copy()
        
        for column in self.price_columns:
            if column not in fixed_data.columns:
                continue
            
            mean_val = fixed_data[column].mean()
            std_val = fixed_data[column].std()
            
            if std_val == 0:
                continue
            
            # 将异常值设为NaN
            z_scores = np.abs((fixed_data[column] - mean_val) / std_val)
            outlier_mask = z_scores > threshold
            fixed_data.loc[outlier_mask, column] = np.nan
            
            # 插值修复
            fixed_data[column] = fixed_data[column].interpolate(method=self.interpolation_method)
        
        return fixed_data
    
    def _remove_unfixable_rows(self, data: pd.DataFrame) -> pd.DataFrame:
        """删除无法修复的行"""
        # 删除所有价格列都为NaN的行
        price_all_nan = data[self.price_columns].isna().all(axis=1)
        
        # 删除符号或交易所为空的行
        symbol_empty = data['symbol'].isna() | (data['symbol'] == '')
        exchange_empty = data['exchange'].isna() | (data['exchange'] == '')
        
        # 删除时间戳无效的行
        timestamp_invalid = data['timestamp'].isna()
        
        # 组合所有删除条件
        rows_to_remove = price_all_nan | symbol_empty | exchange_empty | timestamp_invalid
        
        return data[~rows_to_remove].reset_index(drop=True)
    
    def _parse_interval(self, interval: str) -> Optional[int]:
        """解析时间间隔为秒数"""
        interval_map = {
            '1m': 60,
            '5m': 300,
            '15m': 900,
            '30m': 1800,
            '1h': 3600,
            '2h': 7200,
            '4h': 14400,
            '8h': 28800,
            '12h': 43200,
            '1d': 86400
        }
        
        return interval_map.get(interval.lower())
    
    def _create_result(self, is_valid: bool, issues: List[ValidationIssue], 
                      fixed_data: Optional[pd.DataFrame], original_data: pd.DataFrame, 
                      start_time: float) -> ValidationResult:
        """创建验证结果"""
        duration = time.time() - start_time
        
        # 统计信息
        stats = {
            'total_rows': len(original_data),
            'valid_rows': len(original_data) - len([issue for issue in issues if issue.severity == 'error']),
            'error_rate': len([issue for issue in issues if issue.severity == 'error']) / max(1, len(original_data)),
            'validation_duration': duration,
            'issues_by_type': {}
        }
        
        # 按问题类型统计
        for issue in issues:
            issue_type = issue.issue_type
            if issue_type not in stats['issues_by_type']:
                stats['issues_by_type'][issue_type] = {'error': 0, 'warning': 0, 'info': 0}
            stats['issues_by_type'][issue_type][issue.severity] += 1
        
        logger.info(
            f"数据验证完成: {stats['total_rows']}行, "
            f"错误率: {stats['error_rate']:.2%}, "
            f"耗时: {duration:.3f}秒",
            extra={
                "validation_stats": stats,
                "issues_count": len(issues),
                "is_valid": is_valid
            }
        )
        
        return ValidationResult(
            is_valid=is_valid,
            issues=issues,
            fixed_data=fixed_data,
            stats=stats
        )