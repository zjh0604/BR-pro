#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推荐更新服务
实现增量更新逻辑：基于新插入商单的相似度匹配来更新受影响用户的推荐
"""

import logging
from typing import List, Dict, Any, Set
from services.recommend_service import get_recommendation_service
from services.cache_service import get_cache_service
from business_milvus_db import BusinessMilvusDB
from services.field_normalizer import FieldNormalizer

logger = logging.getLogger(__name__)

class RecommendationUpdateService:
    """推荐更新服务 - 实现增量更新逻辑"""
    
    def __init__(self):
        self.recommendation_service = get_recommendation_service()
        self.cache_service = get_cache_service()
        self.vector_db = BusinessMilvusDB()
    
    def get_affected_users_from_events(self, events: List[Dict[str, Any]]) -> Set[str]:
        """
        从事件库获取受影响的用户列表
        
        实现逻辑：
        1. 如果是新插入商单操作，将新增商单向量化
        2. 在库中进行向量相似度匹配，找出相似的商单（20个）
        3. 根据Redis反向映射查看这些相似商单是否在某些用户的推荐列表中
        4. 返回受影响用户列表
        
        Args:
            events: 事件列表，每个事件包含操作类型和商单数据
            
        Returns:
            Set[str]: 受影响用户ID集合
        """
        try:
            affected_users = set()
            
            for event in events:
                operation_type = event.get('operation_type', '')
                order_data = event.get('order_data', {})
                
                if operation_type == 'INSERT' and order_data:
                    logger.info(f"处理新插入商单事件: {order_data.get('id')}")
                    
                    # 1. 获取受影响用户
                    event_affected_users = self._get_affected_users_for_new_order(order_data)
                    affected_users.update(event_affected_users)
                    
                    logger.info(f"商单 {order_data.get('id')} 影响用户数: {len(event_affected_users)}")
            
            logger.info(f"总受影响用户数: {len(affected_users)}")
            return affected_users
            
        except Exception as e:
            logger.error(f"获取受影响用户失败: {str(e)}")
            return set()
    
    def _get_affected_users_for_new_order(self, order_data: Dict[str, Any]) -> Set[str]:
        """
        获取新插入商单影响的用户列表
        
        Args:
            order_data: 新插入的商单数据
            
        Returns:
            Set[str]: 受影响用户ID集合
        """
        try:
            affected_users = set()
            
            # 1. 标准化商单数据
            normalized_order = FieldNormalizer.normalize_order(order_data)
            
            # 2. 在向量库中进行相似度匹配，找出相似的商单（20个）
            similar_orders = self.vector_db.find_similar_orders_with_filters(
                normalized_order, n_results=20, filters={"state": "WaitReceive"}
            )
            
            if not similar_orders:
                logger.info(f"商单 {order_data.get('id')} 未找到相似商单")
                return affected_users
            
            logger.info(f"商单 {order_data.get('id')} 找到 {len(similar_orders)} 个相似商单")
            
            # 3. 通过Redis反向映射查看这些相似商单在哪些用户的推荐列表中
            for similar_order in similar_orders:
                order_id = similar_order.get('id') or similar_order.get('taskNumber')
                if order_id:
                    # 获取包含该商单的用户列表
                    order_users = self.cache_service.get_order_affected_users(str(order_id))
                    if order_users:
                        affected_users.update(order_users)
                        logger.debug(f"商单 {order_id} 影响用户: {order_users}")
            
            logger.info(f"新商单 {order_data.get('id')} 总影响用户数: {len(affected_users)}")
            return affected_users
            
        except Exception as e:
            logger.error(f"获取新商单影响用户失败: {str(e)}")
            return set()
    
    def update_affected_users_recommendations(self, affected_users: Set[str]) -> Dict[str, Any]:
        """
        更新受影响用户的推荐列表
        
        Args:
            affected_users: 受影响用户ID集合
            
        Returns:
            Dict: 更新结果统计
        """
        try:
            update_stats = {
                "total_users": len(affected_users),
                "success_count": 0,
                "failed_count": 0,
                "success_users": [],
                "failed_users": []
            }
            
            for user_id in affected_users:
                try:
                    logger.info(f"开始更新用户 {user_id} 的推荐列表")
                    
                    # 1. 清除用户现有缓存
                    self.cache_service.invalidate_user_cache(user_id)
                    
                    # 2. 重新生成推荐（触发异步任务）
                    if self._trigger_recommendation_regeneration(user_id):
                        update_stats["success_count"] += 1
                        update_stats["success_users"].append(user_id)
                        logger.info(f"用户 {user_id} 推荐更新任务已触发")
                    else:
                        update_stats["failed_count"] += 1
                        update_stats["failed_users"].append(user_id)
                        logger.error(f"用户 {user_id} 推荐更新任务触发失败")
                        
                except Exception as e:
                    update_stats["failed_count"] += 1
                    update_stats["failed_users"].append(user_id)
                    logger.error(f"更新用户 {user_id} 推荐失败: {str(e)}")
            
            logger.info(f"推荐更新完成: 成功 {update_stats['success_count']}, 失败 {update_stats['failed_count']}")
            return update_stats
            
        except Exception as e:
            logger.error(f"批量更新用户推荐失败: {str(e)}")
            return {
                "total_users": len(affected_users),
                "success_count": 0,
                "failed_count": len(affected_users),
                "success_users": [],
                "failed_users": list(affected_users)
            }
    
    def _trigger_recommendation_regeneration(self, user_id: str) -> bool:
        """
        触发用户推荐重新生成任务
        
        Args:
            user_id: 用户ID
            
        Returns:
            bool: 是否成功触发
        """
        try:
            # 检查异步任务模块可用性
            from services.recommend_service import _check_async_tasks_availability
            
            if _check_async_tasks_availability():
                # 触发异步推荐池预生成任务
                from tasks.recommendation_tasks import enhanced_preload_pagination_pool
                if enhanced_preload_pagination_pool:
                    task_result = enhanced_preload_pagination_pool.delay(user_id, pool_size=150)
                    logger.info(f"✅ 已触发用户 {user_id} 推荐池重新生成任务: task_id={task_result.id}")
                    return True
                else:
                    logger.warning(f"⚠️ enhanced_preload_pagination_pool任务不可用")
                    return False
            else:
                logger.warning(f"⚠️ 异步任务模块未启用")
                return False
                
        except Exception as e:
            logger.error(f"触发推荐重新生成任务失败: {str(e)}")
            return False
    
    def process_events_and_update_recommendations(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        处理事件并更新推荐的主方法
        
        Args:
            events: 事件列表
            
        Returns:
            Dict: 处理结果统计
        """
        try:
            logger.info(f"开始处理 {len(events)} 个事件")
            
            # 1. 获取受影响的用户
            affected_users = self.get_affected_users_from_events(events)
            
            if not affected_users:
                logger.info("没有受影响的用户，无需更新推荐")
                return {
                    "events_processed": len(events),
                    "affected_users": 0,
                    "update_stats": None
                }
            
            # 2. 更新受影响用户的推荐
            update_stats = self.update_affected_users_recommendations(affected_users)
            
            result = {
                "events_processed": len(events),
                "affected_users": len(affected_users),
                "update_stats": update_stats
            }
            
            logger.info(f"事件处理完成: {result}")
            return result
            
        except Exception as e:
            logger.error(f"处理事件并更新推荐失败: {str(e)}")
            return {
                "events_processed": len(events),
                "affected_users": 0,
                "update_stats": None,
                "error": str(e)
            }

# 创建单例实例
recommendation_update_service = RecommendationUpdateService()

def get_recommendation_update_service() -> RecommendationUpdateService:
    """获取推荐更新服务的单例实例"""
    return recommendation_update_service 