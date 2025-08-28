#!/usr/bin/env python3
"""
LLM异步任务监控脚本
用于监控任务执行状态、性能指标和健康检查
"""

import redis
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any
from services.cache_service import get_cache_service

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LLMTaskMonitor:
    """LLM任务监控器"""
    
    def __init__(self):
        self.cache_service = get_cache_service()
        self.redis_client = self.cache_service.redis_client
    
    def get_task_statistics(self) -> Dict[str, Any]:
        """获取任务统计信息"""
        try:
            # 获取所有任务键
            task_keys = self.redis_client.keys("task:*")
            
            stats = {
                "total_tasks": len(task_keys),
                "pending_tasks": 0,
                "processing_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "completed_with_fallback_tasks": 0,
                "tasks_by_user": {},
                "avg_processing_time": 0,
                "oldest_pending_task": None,
                "failed_task_details": []
            }
            
            processing_times = []
            oldest_pending_time = None
            
            for key in task_keys:
                try:
                    task_data = self.redis_client.get(key)
                    if task_data:
                        task_info = json.loads(task_data)
                        status = task_info.get("status")
                        user_id = key.split(":")[1]
                        
                        # 统计状态
                        if status == "pending":
                            stats["pending_tasks"] += 1
                            task_time = task_info.get("updated_at")
                            if task_time and (oldest_pending_time is None or task_time < oldest_pending_time):
                                oldest_pending_time = task_time
                                stats["oldest_pending_task"] = {
                                    "task_id": task_info.get("task_id"),
                                    "user_id": user_id,
                                    "pending_since": datetime.fromtimestamp(task_time).isoformat()
                                }
                        elif status == "processing":
                            stats["processing_tasks"] += 1
                        elif status == "completed":
                            stats["completed_tasks"] += 1
                            if "processing_time" in task_info:
                                processing_times.append(task_info["processing_time"])
                        elif status == "failed":
                            stats["failed_tasks"] += 1
                            stats["failed_task_details"].append({
                                "task_id": task_info.get("task_id"),
                                "user_id": user_id,
                                "error": task_info.get("error", "Unknown error"),
                                "retry_count": task_info.get("retry_count", 0)
                            })
                        elif status == "completed_with_fallback":
                            stats["completed_with_fallback_tasks"] += 1
                        
                        # 按用户统计
                        if user_id not in stats["tasks_by_user"]:
                            stats["tasks_by_user"][user_id] = {"total": 0, "pending": 0, "processing": 0, "completed": 0, "failed": 0}
                        stats["tasks_by_user"][user_id]["total"] += 1
                        stats["tasks_by_user"][user_id][status] += 1
                        
                except Exception as e:
                    logger.error(f"解析任务数据失败: {key}, error: {str(e)}")
            
            # 计算平均处理时间
            if processing_times:
                stats["avg_processing_time"] = sum(processing_times) / len(processing_times)
            
            return stats
            
        except Exception as e:
            logger.error(f"获取任务统计失败: {str(e)}")
            return {}
    
    def cleanup_expired_tasks(self, hours: int = 24) -> int:
        """清理过期任务"""
        try:
            task_keys = self.redis_client.keys("task:*")
            cleaned_count = 0
            cutoff_time = time.time() - (hours * 3600)
            
            for key in task_keys:
                try:
                    task_data = self.redis_client.get(key)
                    if task_data:
                        task_info = json.loads(task_data)
                        updated_at = task_info.get("updated_at", 0)
                        
                        if updated_at < cutoff_time:
                            self.redis_client.delete(key)
                            cleaned_count += 1
                            logger.info(f"清理过期任务: {key}")
                            
                except Exception as e:
                    logger.error(f"清理任务失败: {key}, error: {str(e)}")
            
            logger.info(f"清理完成，共清理 {cleaned_count} 个过期任务")
            return cleaned_count
            
        except Exception as e:
            logger.error(f"清理过期任务失败: {str(e)}")
            return 0
    
    def get_cache_statistics(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        try:
            initial_keys = self.redis_client.keys("rec:initial:*")
            final_keys = self.redis_client.keys("rec:final:*")
            
            stats = {
                "initial_cache_count": len(initial_keys),
                "final_cache_count": len(final_keys),
                "cache_hit_potential": len(final_keys) / max(len(initial_keys), 1) * 100,
                "users_with_cache": set()
            }
            
            # 统计有缓存的用户
            for key in initial_keys + final_keys:
                user_id = key.split(":")[2]
                stats["users_with_cache"].add(user_id)
            
            stats["users_with_cache_count"] = len(stats["users_with_cache"])
            stats["users_with_cache"] = list(stats["users_with_cache"])
            
            return stats
            
        except Exception as e:
            logger.error(f"获取缓存统计失败: {str(e)}")
            return {}
    
    def health_check(self) -> Dict[str, Any]:
        """系统健康检查"""
        health = {
            "redis_connection": False,
            "stuck_tasks": [],
            "cache_memory_usage": 0,
            "recommendations": []
        }
        
        try:
            # 检查Redis连接
            health["redis_connection"] = self.cache_service.ping()
            
            # 检查卡住的任务（处理中超过30分钟）
            task_keys = self.redis_client.keys("task:*")
            cutoff_time = time.time() - 1800  # 30分钟前
            
            for key in task_keys:
                try:
                    task_data = self.redis_client.get(key)
                    if task_data:
                        task_info = json.loads(task_data)
                        status = task_info.get("status")
                        updated_at = task_info.get("updated_at", 0)
                        
                        if status == "processing" and updated_at < cutoff_time:
                            health["stuck_tasks"].append({
                                "task_id": task_info.get("task_id"),
                                "user_id": key.split(":")[1],
                                "stuck_duration": time.time() - updated_at
                            })
                except Exception as e:
                    logger.error(f"检查任务状态失败: {key}, error: {str(e)}")
            
            # 检查内存使用情况
            info = self.redis_client.info('memory')
            health["cache_memory_usage"] = info.get('used_memory_human', 'Unknown')
            
            # 生成建议
            if not health["redis_connection"]:
                health["recommendations"].append("Redis连接失败，请检查Redis服务")
            
            if len(health["stuck_tasks"]) > 0:
                health["recommendations"].append(f"发现 {len(health['stuck_tasks'])} 个卡住的任务，建议重启Celery Worker")
            
            if len(health["stuck_tasks"]) == 0 and health["redis_connection"]:
                health["recommendations"].append("系统运行正常")
                
        except Exception as e:
            logger.error(f"健康检查失败: {str(e)}")
            health["recommendations"].append(f"健康检查失败: {str(e)}")
        
        return health
    
    def print_report(self):
        """打印监控报告"""
        print("=" * 80)
        print("LLM异步任务监控报告")
        print("=" * 80)
        print(f"报告时间: {datetime.now().isoformat()}")
        print()
        
        # 任务统计
        task_stats = self.get_task_statistics()
        print("【任务统计】")
        print(f"总任务数: {task_stats.get('total_tasks', 0)}")
        print(f"等待中: {task_stats.get('pending_tasks', 0)}")
        print(f"处理中: {task_stats.get('processing_tasks', 0)}")
        print(f"已完成: {task_stats.get('completed_tasks', 0)}")
        print(f"失败: {task_stats.get('failed_tasks', 0)}")
        print(f"降级完成: {task_stats.get('completed_with_fallback_tasks', 0)}")
        
        if task_stats.get('avg_processing_time'):
            print(f"平均处理时间: {task_stats['avg_processing_time']:.2f}秒")
        
        if task_stats.get('oldest_pending_task'):
            print(f"最早等待任务: {task_stats['oldest_pending_task']}")
        print()
        
        # 缓存统计
        cache_stats = self.get_cache_statistics()
        print("【缓存统计】")
        print(f"初步推荐缓存: {cache_stats.get('initial_cache_count', 0)}")
        print(f"精准推荐缓存: {cache_stats.get('final_cache_count', 0)}")
        print(f"有缓存的用户数: {cache_stats.get('users_with_cache_count', 0)}")
        print(f"缓存命中潜力: {cache_stats.get('cache_hit_potential', 0):.1f}%")
        print()
        
        # 健康检查
        health = self.health_check()
        print("【健康检查】")
        print(f"Redis连接: {'✓' if health.get('redis_connection') else '✗'}")
        print(f"内存使用: {health.get('cache_memory_usage', 'Unknown')}")
        print(f"卡住的任务: {len(health.get('stuck_tasks', []))}")
        
        if health.get('recommendations'):
            print("建议:")
            for rec in health['recommendations']:
                print(f"  - {rec}")
        print()
        
        # 失败任务详情
        if task_stats.get('failed_task_details'):
            print("【失败任务详情】")
            for task in task_stats['failed_task_details'][:5]:  # 只显示前5个
                print(f"  Task ID: {task['task_id']}")
                print(f"  User ID: {task['user_id']}")
                print(f"  Error: {task['error']}")
                print(f"  Retries: {task['retry_count']}")
                print()
        
        print("=" * 80)

def main():
    """主函数"""
    monitor = LLMTaskMonitor()
    
    # 打印监控报告
    monitor.print_report()
    
    # 清理过期任务
    cleaned = monitor.cleanup_expired_tasks(24)  # 清理24小时前的任务
    if cleaned > 0:
        print(f"清理了 {cleaned} 个过期任务")

if __name__ == "__main__":
    main() 