 #!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API响应监控工具 - 实时查看为不同用户返回的推荐数据
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime
import json
from typing import Dict, List, Any
import threading
import time
from collections import defaultdict, deque

class APIResponseMonitor:
    """API响应监控器"""
    
    def __init__(self, max_history=100):
        self.responses = deque(maxlen=max_history)  # 保存最近的响应
        self.user_stats = defaultdict(lambda: {
            'request_count': 0,
            'success_count': 0,
            'empty_response_count': 0,
            'last_request_time': None,
            'last_response_summary': None
        })
        self.lock = threading.Lock()
    
    def log_response(self, user_id: str, endpoint: str, response_data: Dict[str, Any], 
                    request_params: Dict[str, Any] = None):
        """记录API响应"""
        with self.lock:
            timestamp = datetime.now()
            
            # 分析响应数据
            user_orders = response_data.get('user_orders', [])
            recommended_orders = response_data.get('recommended_orders', [])
            pagination = response_data.get('pagination', {})
            
            # 创建响应记录
            response_record = {
                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                'user_id': user_id,
                'endpoint': endpoint,
                'request_params': request_params or {},
                'user_orders_count': len(user_orders),
                'recommended_orders_count': len(recommended_orders),
                'pagination': pagination,
                'is_empty': len(recommended_orders) == 0,
                'response_summary': self._create_response_summary(response_data)
            }
            
            # 添加到历史记录
            self.responses.append(response_record)
            
            # 更新用户统计
            stats = self.user_stats[user_id]
            stats['request_count'] += 1
            stats['last_request_time'] = timestamp
            
            if len(recommended_orders) > 0:
                stats['success_count'] += 1
            else:
                stats['empty_response_count'] += 1
            
            stats['last_response_summary'] = response_record['response_summary']
            
            return response_record
    
    def _create_response_summary(self, response_data: Dict[str, Any]) -> Dict[str, Any]:
        """创建响应摘要"""
        recommended_orders = response_data.get('recommended_orders', [])
        
        summary = {
            'total_recommendations': len(recommended_orders),
            'sample_recommendations': []
        }
        
        # 获取前3个推荐的摘要
        for order in recommended_orders[:3]:
            summary['sample_recommendations'].append({
                'order_id': order.get('order_id'),
                'user_id': order.get('user_id'),
                'title': order.get('wish_title', 'N/A')[:50],  # 截断长标题
                'role': order.get('corresponding_role', 'N/A'),
                'classification': order.get('classification', 'N/A'),
                'strategy': order.get('recommendation_strategy', 'unknown')
            })
        
        # 统计推荐策略分布
        strategy_counts = defaultdict(int)
        for order in recommended_orders:
            strategy = order.get('recommendation_strategy', 'unknown')
            strategy_counts[strategy] += 1
        
        summary['strategy_distribution'] = dict(strategy_counts)
        
        return summary
    
    def get_user_summary(self, user_id: str = None) -> Dict[str, Any]:
        """获取用户摘要"""
        with self.lock:
            if user_id:
                return {user_id: dict(self.user_stats[user_id])}
            else:
                return {uid: dict(stats) for uid, stats in self.user_stats.items()}
    
    def get_recent_responses(self, user_id: str = None, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的响应记录"""
        with self.lock:
            responses = list(self.responses)
            
            if user_id:
                responses = [r for r in responses if r['user_id'] == user_id]
            
            return responses[-limit:]
    
    def print_summary(self):
        """打印监控摘要"""
        print("\n" + "=" * 80)
        print("API响应监控摘要")
        print("=" * 80)
        
        # 打印用户统计
        user_summary = self.get_user_summary()
        
        print(f"\n总用户数: {len(user_summary)}")
        print("\n用户统计:")
        print("-" * 80)
        print(f"{'用户ID':<10} {'请求次数':<10} {'成功次数':<10} {'空响应次数':<12} {'最后请求时间':<20}")
        print("-" * 80)
        
        for user_id, stats in user_summary.items():
            last_time = stats['last_request_time']
            last_time_str = last_time.strftime('%Y-%m-%d %H:%M:%S') if last_time else 'N/A'
            
            print(f"{user_id:<10} {stats['request_count']:<10} "
                  f"{stats['success_count']:<10} {stats['empty_response_count']:<12} "
                  f"{last_time_str:<20}")
        
        # 打印最近的响应
        print("\n\n最近的响应记录:")
        print("-" * 80)
        
        recent = self.get_recent_responses(limit=5)
        for i, response in enumerate(recent, 1):
            print(f"\n{i}. 时间: {response['timestamp']}")
            print(f"   用户: {response['user_id']}")
            print(f"   端点: {response['endpoint']}")
            print(f"   请求参数: {response['request_params']}")
            print(f"   用户商单数: {response['user_orders_count']}")
            print(f"   推荐商单数: {response['recommended_orders_count']}")
            print(f"   是否空响应: {'是' if response['is_empty'] else '否'}")
            
            if response['response_summary']['sample_recommendations']:
                print("   推荐样例:")
                for j, rec in enumerate(response['response_summary']['sample_recommendations'], 1):
                    print(f"     {j}. {rec['title']} (用户{rec['user_id']}, {rec['strategy']})")
            
            if response['response_summary']['strategy_distribution']:
                print(f"   策略分布: {response['response_summary']['strategy_distribution']}")

# 全局监控器实例
monitor = APIResponseMonitor()

def create_monitored_endpoint(original_func):
    """创建被监控的端点装饰器"""
    def wrapper(user_id: str, *args, **kwargs):
        # 调用原始函数
        response = original_func(user_id, *args, **kwargs)
        
        # 记录响应
        endpoint_name = original_func.__name__
        request_params = {
            'page': kwargs.get('page', 1),
            'page_size': kwargs.get('page_size', 10)
        }
        
        monitor.log_response(user_id, endpoint_name, response, request_params)
        
        return response
    
    return wrapper

def start_monitor_dashboard():
    """启动监控仪表板（在单独线程中运行）"""
    def dashboard_loop():
        while True:
            time.sleep(30)  # 每30秒更新一次
            os.system('cls' if os.name == 'nt' else 'clear')
            monitor.print_summary()
    
    thread = threading.Thread(target=dashboard_loop, daemon=True)
    thread.start()

if __name__ == "__main__":
    # 测试监控器
    print("API响应监控器测试")
    
    # 模拟一些响应
    test_responses = [
        {
            'user_id': '91',
            'response': {
                'user_orders': [],
                'recommended_orders': [],
                'pagination': {'current_page': 1, 'total_pages': 0}
            }
        },
        {
            'user_id': '372',
            'response': {
                'user_orders': [{'order_id': 1}],
                'recommended_orders': [
                    {'order_id': 100, 'user_id': '10', 'wish_title': '测试商单1', 'recommendation_strategy': 'similarity'},
                    {'order_id': 101, 'user_id': '11', 'wish_title': '测试商单2', 'recommendation_strategy': 'platform_orders'}
                ],
                'pagination': {'current_page': 1, 'total_pages': 5}
            }
        }
    ]
    
    for test in test_responses:
        monitor.log_response(
            test['user_id'], 
            'get_paginated_recommendations',
            test['response'],
            {'page': 1, 'page_size': 10}
        )
    
    monitor.print_summary()