"""
CryptoDataValidator 数据验证器单元测试
TDD: 先写测试定义验证行为，再实现
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any

# from src.validators.crypto_data_validator import CryptoDataValidator, ValidationResult, ValidationIssue


class TestCryptoDataValidator:
    """CryptoDataValidator 数据验证器测试"""
    
    @pytest.fixture
    def validator(self):
        """创建验证器实例"""
        from src.validators.crypto_data_validator import CryptoDataValidator
        return CryptoDataValidator()
    
    @pytest.fixture
    def valid_kline_data(self):
        """有效的K线数据"""
        now = datetime.now()
        return pd.DataFrame({
            'timestamp': [now - timedelta(minutes=i) for i in range(5)],
            'open': [50000.0, 50010.0, 50020.0, 50030.0, 50040.0],
            'high': [50100.0, 50110.0, 50120.0, 50130.0, 50140.0],
            'low': [49900.0, 49910.0, 49920.0, 49930.0, 49940.0],
            'close': [50050.0, 50060.0, 50070.0, 50080.0, 50090.0],
            'volume': [100.0, 101.0, 102.0, 103.0, 104.0],
            'symbol': ['BTC/USDT'] * 5,
            'exchange': ['binance'] * 5
        })
    
    @pytest.fixture
    def invalid_kline_data(self):
        """包含各种问题的K线数据"""
        now = datetime.now()
        return pd.DataFrame({
            'timestamp': [now - timedelta(minutes=i) for i in range(5)],
            'open': [50000.0, np.nan, 50020.0, -50030.0, 50040.0],  # NaN和负数
            'high': [49900.0, 50110.0, 50120.0, 50130.0, 50140.0],  # high < low/open/close
            'low': [50100.0, 49910.0, 49920.0, 49930.0, 49940.0],   # low > high/open/close
            'close': [50050.0, 50060.0, np.inf, 50080.0, 50090.0],  # 无穷大
            'volume': [-100.0, 101.0, 0.0, 103.0, 104.0],           # 负数和零成交量
            'symbol': ['BTC/USDT', '', 'BTC/USDT', 'BTC/USDT', 'BTC/USDT'],  # 空符号
            'exchange': ['binance'] * 5
        })

    # ===============================
    # 基本验证测试
    # ===============================
    
    @pytest.mark.unit
    def test_validate_valid_data_passes(self, validator, valid_kline_data):
        """测试有效数据通过验证"""
        result = validator.validate_kline_data(valid_kline_data)
        
        assert result.is_valid == True
        assert len(result.issues) == 0
        assert result.fixed_data is None  # 没有修复需求
        assert result.stats['total_rows'] == 5
        assert result.stats['valid_rows'] == 5
        assert result.stats['error_rate'] == 0.0
    
    @pytest.mark.unit
    def test_validate_invalid_data_detects_issues(self, validator, invalid_kline_data):
        """测试无效数据能检测出问题"""
        result = validator.validate_kline_data(invalid_kline_data)
        
        assert result.is_valid == False
        assert len(result.issues) > 0
        assert result.fixed_data is not None  # 应该有修复版本
        assert result.stats['total_rows'] == 5
        assert result.stats['valid_rows'] < 5
        assert result.stats['error_rate'] > 0.0

    # ===============================
    # 具体验证规则测试
    # ===============================
    
    @pytest.mark.unit
    def test_detect_missing_values(self, validator):
        """测试检测缺失值"""
        data = pd.DataFrame({
            'timestamp': [datetime.now()],
            'open': [np.nan],
            'high': [50000.0],
            'low': [49000.0], 
            'close': [49500.0],
            'volume': [100.0],
            'symbol': ['BTC/USDT'],
            'exchange': ['binance']
        })
        
        result = validator.validate_kline_data(data)
        
        assert result.is_valid == False
        nan_issues = [issue for issue in result.issues if issue.issue_type == 'missing_value']
        assert len(nan_issues) > 0
        assert 'open' in str(nan_issues[0].details)
    
    @pytest.mark.unit
    def test_detect_negative_prices(self, validator):
        """测试检测负价格"""
        data = pd.DataFrame({
            'timestamp': [datetime.now()],
            'open': [-50000.0],
            'high': [50000.0],
            'low': [49000.0],
            'close': [49500.0],
            'volume': [100.0],
            'symbol': ['BTC/USDT'],
            'exchange': ['binance']
        })
        
        result = validator.validate_kline_data(data)
        
        assert result.is_valid == False
        negative_issues = [issue for issue in result.issues if issue.issue_type == 'negative_price']
        assert len(negative_issues) > 0
        assert 'open' in str(negative_issues[0].details)
    
    @pytest.mark.unit
    def test_detect_ohlc_inconsistencies(self, validator):
        """测试检测OHLC价格逻辑不一致"""
        data = pd.DataFrame({
            'timestamp': [datetime.now()],
            'open': [50000.0],
            'high': [49000.0],  # high < open (错误)
            'low': [51000.0],   # low > open (错误)
            'close': [49500.0],
            'volume': [100.0],
            'symbol': ['BTC/USDT'],
            'exchange': ['binance']
        })
        
        result = validator.validate_kline_data(data)
        
        assert result.is_valid == False
        ohlc_issues = [issue for issue in result.issues if issue.issue_type == 'ohlc_inconsistency']
        assert len(ohlc_issues) > 0
    
    @pytest.mark.unit
    def test_detect_infinite_values(self, validator):
        """测试检测无穷大值"""
        data = pd.DataFrame({
            'timestamp': [datetime.now()],
            'open': [50000.0],
            'high': [np.inf],
            'low': [49000.0],
            'close': [49500.0],
            'volume': [100.0],
            'symbol': ['BTC/USDT'],
            'exchange': ['binance']
        })
        
        result = validator.validate_kline_data(data)
        
        assert result.is_valid == False
        inf_issues = [issue for issue in result.issues if issue.issue_type == 'infinite_value']
        assert len(inf_issues) > 0
        assert 'high' in str(inf_issues[0].details)
    
    @pytest.mark.unit
    def test_detect_negative_or_zero_volume(self, validator):
        """测试检测负数或零成交量"""
        data = pd.DataFrame({
            'timestamp': [datetime.now(), datetime.now()],
            'open': [50000.0, 50100.0],
            'high': [50100.0, 50200.0],
            'low': [49900.0, 50000.0],
            'close': [50050.0, 50150.0],
            'volume': [-100.0, 0.0],  # 负数和零成交量
            'symbol': ['BTC/USDT', 'BTC/USDT'],
            'exchange': ['binance', 'binance']
        })
        
        result = validator.validate_kline_data(data)
        
        assert result.is_valid == False
        volume_issues = [issue for issue in result.issues if issue.issue_type == 'invalid_volume']
        assert len(volume_issues) >= 1  # 至少检测到负数成交量

    # ===============================
    # 数据修复测试
    # ===============================
    
    @pytest.mark.unit
    def test_fix_missing_values_with_interpolation(self, validator):
        """测试使用插值修复缺失值"""
        data = pd.DataFrame({
            'timestamp': [datetime.now() - timedelta(minutes=i) for i in [2, 1, 0]],
            'open': [50000.0, np.nan, 50200.0],
            'high': [50100.0, np.nan, 50300.0],
            'low': [49900.0, np.nan, 50100.0],
            'close': [50050.0, np.nan, 50250.0],
            'volume': [100.0, np.nan, 120.0],
            'symbol': ['BTC/USDT'] * 3,
            'exchange': ['binance'] * 3
        })
        
        result = validator.validate_kline_data(data, fix_issues=True)
        
        assert result.fixed_data is not None
        # 中间行应该被插值修复
        fixed_row = result.fixed_data.iloc[1]
        assert not pd.isna(fixed_row['open'])
        assert not pd.isna(fixed_row['high'])
        assert not pd.isna(fixed_row['low'])
        assert not pd.isna(fixed_row['close'])
        # 检查插值是否合理
        assert 50000.0 < fixed_row['open'] < 50200.0
    
    @pytest.mark.unit
    def test_fix_ohlc_inconsistencies(self, validator):
        """测试修复OHLC不一致"""
        data = pd.DataFrame({
            'timestamp': [datetime.now()],
            'open': [50000.0],
            'high': [49000.0],  # 错误：high < open
            'low': [51000.0],   # 错误：low > open
            'close': [49500.0],
            'volume': [100.0],
            'symbol': ['BTC/USDT'],
            'exchange': ['binance']
        })
        
        result = validator.validate_kline_data(data, fix_issues=True)
        
        assert result.fixed_data is not None
        fixed_row = result.fixed_data.iloc[0]
        
        # 修复后应该满足 low <= open/close <= high
        assert fixed_row['low'] <= fixed_row['open']
        assert fixed_row['low'] <= fixed_row['close']
        assert fixed_row['high'] >= fixed_row['open']
        assert fixed_row['high'] >= fixed_row['close']
    
    @pytest.mark.unit
    def test_remove_invalid_rows_when_unfixable(self, validator):
        """测试删除无法修复的行"""
        data = pd.DataFrame({
            'timestamp': [datetime.now(), datetime.now()],
            'open': [50000.0, np.nan],
            'high': [50100.0, np.nan],
            'low': [49900.0, np.nan],
            'close': [50050.0, np.nan],
            'volume': [100.0, np.nan],
            'symbol': ['BTC/USDT', ''],  # 空符号
            'exchange': ['binance', 'binance']
        })
        
        result = validator.validate_kline_data(data, fix_issues=True)
        
        assert result.fixed_data is not None
        # 第二行应该被删除（太多缺失值 + 空符号）
        assert len(result.fixed_data) == 1
        assert result.fixed_data.iloc[0]['symbol'] == 'BTC/USDT'

    # ===============================
    # 高级验证测试
    # ===============================
    
    @pytest.mark.unit
    def test_detect_price_spike_outliers(self, validator):
        """测试检测价格异常波动"""
        # 创建包含异常价格波动的数据
        base_price = 50000.0
        data = pd.DataFrame({
            'timestamp': [datetime.now() - timedelta(minutes=i) for i in range(5, 0, -1)],
            'open': [base_price, base_price, base_price * 10, base_price, base_price],  # 异常高价
            'high': [base_price * 1.01, base_price * 1.01, base_price * 10.1, base_price * 1.01, base_price * 1.01],
            'low': [base_price * 0.99, base_price * 0.99, base_price * 9.9, base_price * 0.99, base_price * 0.99],
            'close': [base_price, base_price, base_price * 10.05, base_price, base_price],
            'volume': [100.0] * 5,
            'symbol': ['BTC/USDT'] * 5,
            'exchange': ['binance'] * 5
        })
        
        # 启用异常检测
        result = validator.validate_kline_data(data, detect_outliers=True, outlier_threshold=3.0)
        
        assert result.is_valid == False
        outlier_issues = [issue for issue in result.issues if issue.issue_type == 'price_outlier']
        assert len(outlier_issues) > 0
    
    @pytest.mark.unit  
    def test_detect_timestamp_gaps(self, validator):
        """测试检测时间戳缺口"""
        now = datetime.now()
        data = pd.DataFrame({
            'timestamp': [
                now - timedelta(minutes=4),
                now - timedelta(minutes=3),
                # 缺少 minutes=2 的数据点
                now - timedelta(minutes=1),
                now
            ],
            'open': [50000.0] * 4,
            'high': [50100.0] * 4,
            'low': [49900.0] * 4,
            'close': [50050.0] * 4,
            'volume': [100.0] * 4,
            'symbol': ['BTC/USDT'] * 4,
            'exchange': ['binance'] * 4
        })
        
        result = validator.validate_kline_data(data, check_timestamp_gaps=True, expected_interval='1m')
        
        assert result.is_valid == False
        gap_issues = [issue for issue in result.issues if issue.issue_type == 'timestamp_gap']
        assert len(gap_issues) > 0
    
    @pytest.mark.unit
    def test_validate_with_custom_rules(self, validator):
        """测试自定义验证规则"""
        def custom_rule(df: pd.DataFrame) -> List[Dict[str, Any]]:
            """自定义规则：成交量不能超过1000"""
            issues = []
            high_volume_rows = df[df['volume'] > 1000]
            
            for idx, row in high_volume_rows.iterrows():
                issues.append({
                    'row_index': idx,
                    'issue_type': 'high_volume',
                    'severity': 'warning', 
                    'details': f"Volume {row['volume']} exceeds threshold 1000"
                })
            return issues
        
        data = pd.DataFrame({
            'timestamp': [datetime.now()],
            'open': [50000.0],
            'high': [50100.0],
            'low': [49900.0],
            'close': [50050.0],
            'volume': [2000.0],  # 超过阈值
            'symbol': ['BTC/USDT'],
            'exchange': ['binance']
        })
        
        result = validator.validate_kline_data(data, custom_rules=[custom_rule])
        
        assert result.is_valid == False
        custom_issues = [issue for issue in result.issues if issue.issue_type == 'high_volume']
        assert len(custom_issues) > 0

    # ===============================
    # 性能测试
    # ===============================
    
    @pytest.mark.unit
    def test_validate_large_dataset_performance(self, validator):
        """测试大数据集验证性能"""
        # 创建10000行数据
        rows = 10000
        now = datetime.now()
        
        data = pd.DataFrame({
            'timestamp': [now - timedelta(seconds=i) for i in range(rows)],
            'open': np.random.uniform(49000, 51000, rows),
            'high': np.random.uniform(51000, 52000, rows),
            'low': np.random.uniform(48000, 49000, rows),
            'close': np.random.uniform(49000, 51000, rows),
            'volume': np.random.uniform(50, 200, rows),
            'symbol': ['BTC/USDT'] * rows,
            'exchange': ['binance'] * rows
        })
        
        import time
        start_time = time.time()
        result = validator.validate_kline_data(data)
        duration = time.time() - start_time
        
        # 验证应该在合理时间内完成（<5秒）
        assert duration < 5.0
        assert result.stats['total_rows'] == rows

    # ===============================
    # 统计信息测试
    # ===============================
    
    @pytest.mark.unit
    def test_validation_statistics(self, validator, invalid_kline_data):
        """测试验证统计信息"""
        result = validator.validate_kline_data(invalid_kline_data)
        
        stats = result.stats
        assert 'total_rows' in stats
        assert 'valid_rows' in stats
        assert 'error_rate' in stats
        assert 'issues_by_type' in stats
        assert 'validation_duration' in stats
        
        assert stats['total_rows'] == 5
        assert stats['valid_rows'] < stats['total_rows']
        assert 0.0 < stats['error_rate'] <= 1.0
        assert isinstance(stats['issues_by_type'], dict)
        assert stats['validation_duration'] > 0

    # ===============================
    # 配置测试
    # ===============================
    
    @pytest.mark.unit
    def test_validator_with_custom_config(self):
        """测试自定义配置的验证器"""
        from src.validators.crypto_data_validator import CryptoDataValidator
        
        config = {
            'enable_outlier_detection': True,
            'outlier_threshold': 2.5,
            'enable_ohlc_validation': True,
            'enable_volume_validation': True,
            'min_price': 0.001,
            'max_price': 1000000.0,
            'interpolation_method': 'linear'
        }
        
        validator = CryptoDataValidator(**config)
        
        # 验证配置已正确设置
        assert validator.enable_outlier_detection == True
        assert validator.outlier_threshold == 2.5
        assert validator.min_price == 0.001
        assert validator.max_price == 1000000.0


class TestValidationResult:
    """ValidationResult 类测试"""
    
    @pytest.mark.unit
    def test_validation_result_creation(self):
        """测试验证结果创建"""
        from src.validators.crypto_data_validator import ValidationResult, ValidationIssue
        
        issues = [
            ValidationIssue(
                row_index=0,
                issue_type='missing_value',
                severity='error',
                details='Missing open price'
            )
        ]
        
        result = ValidationResult(
            is_valid=False,
            issues=issues,
            fixed_data=None,
            stats={
                'total_rows': 10,
                'valid_rows': 9,
                'error_rate': 0.1
            }
        )
        
        assert result.is_valid == False
        assert len(result.issues) == 1
        assert result.issues[0].issue_type == 'missing_value'
        assert result.stats['error_rate'] == 0.1


class TestValidationIssue:
    """ValidationIssue 类测试"""
    
    @pytest.mark.unit
    def test_validation_issue_creation(self):
        """测试验证问题创建"""
        from src.validators.crypto_data_validator import ValidationIssue
        
        issue = ValidationIssue(
            row_index=5,
            issue_type='negative_price',
            severity='error',
            details='Open price is negative: -50000.0'
        )
        
        assert issue.row_index == 5
        assert issue.issue_type == 'negative_price'
        assert issue.severity == 'error'
        assert 'negative' in issue.details
    
    @pytest.mark.unit
    def test_validation_issue_string_representation(self):
        """测试验证问题字符串表示"""
        from src.validators.crypto_data_validator import ValidationIssue
        
        issue = ValidationIssue(
            row_index=3,
            issue_type='ohlc_inconsistency',
            severity='warning',
            details='High price is lower than open price'
        )
        
        str_repr = str(issue)
        assert 'Row 3' in str_repr
        assert 'ohlc_inconsistency' in str_repr
        assert 'warning' in str_repr


# ===============================
# 辅助测试fixtures
# ===============================

@pytest.fixture
def sample_btc_data():
    """BTC样本数据"""
    now = datetime.now()
    return pd.DataFrame({
        'timestamp': [now - timedelta(hours=i) for i in range(24, 0, -1)],
        'open': [50000.0 + i * 10 for i in range(24)],
        'high': [50100.0 + i * 10 for i in range(24)],
        'low': [49900.0 + i * 10 for i in range(24)],
        'close': [50050.0 + i * 10 for i in range(24)],
        'volume': [100.0 + i for i in range(24)],
        'symbol': ['BTC/USDT'] * 24,
        'exchange': ['binance'] * 24
    })


@pytest.fixture
def sample_eth_data():
    """ETH样本数据"""
    now = datetime.now()
    return pd.DataFrame({
        'timestamp': [now - timedelta(hours=i) for i in range(12, 0, -1)],
        'open': [3000.0 + i * 5 for i in range(12)],
        'high': [3100.0 + i * 5 for i in range(12)],
        'low': [2900.0 + i * 5 for i in range(12)],
        'close': [3050.0 + i * 5 for i in range(12)],
        'volume': [200.0 + i * 2 for i in range(12)],
        'symbol': ['ETH/USDT'] * 12,
        'exchange': ['binance'] * 12
    })