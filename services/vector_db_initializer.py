#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
向量数据库初始化服务
用于从后端API获取商单数据并初始化向量数据库
"""

import logging
import time
from typing import List, Dict, Any, Optional
from services.backend_api_client import BackendAPIClient
from business_milvus_db import BusinessMilvusDB

logger = logging.getLogger(__name__)

class VectorDBInitializer:
    """向量数据库初始化器"""
    
    def __init__(self):
        self.backend_client = BackendAPIClient()
        self.milvus_db = BusinessMilvusDB()
    
    def initialize_vector_database(self, max_orders: int = None) -> Dict[str, Any]:
        """
        初始化向量数据库
        
        Args:
            max_orders: 最大初始化商单数量，None表示根据环境自动判断
            
        Returns:
            Dict: 初始化结果
        """
        try:
            logger.info("🚀 开始初始化向量数据库...")
            start_time = time.time()
            
            # 1. 从后端获取商单数据
            logger.info("📥 从后端API获取商单数据...")
            orders = self.backend_client.get_all_orders()
            
            if not orders:
                logger.error("❌ 无法从后端获取商单数据")
                return {
                    "success": False,
                    "error": "无法从后端获取商单数据",
                    "orders_count": 0,
                    "inserted_count": 0,
                    "processing_time": 0
                }
            
            logger.info(f"✅ 从后端获取到 {len(orders)} 个商单")
            
            # 2. 根据环境自动设置max_orders
            import os
            # 强制检查测试环境
            testing_env = os.getenv('TESTING', 'false').lower()
            if testing_env in ['true', '1', 'yes']:
                # 测试环境：插入100个
                max_orders = 100
                logger.info("🔧 测试环境：限制插入100个商单")
            else:
                # 生产环境：插入全部
                max_orders = len(orders)
                logger.info("🚀 生产环境：插入全部商单")
            
            # 3. 检查Milvus连接
            if not self.milvus_db.collection:
                logger.error("❌ Milvus集合不存在")
                return {
                    "success": False,
                    "error": "Milvus集合不存在",
                    "orders_count": len(orders),
                    "inserted_count": 0,
                    "processing_time": 0
                }
            
            logger.info(f"✅ Milvus连接正常，集合: {self.milvus_db.collection.name}")
            
            # 4. 清空现有数据（可选）
            logger.info("🧹 清空现有向量数据...")
            try:
                self.milvus_db.clear_all_orders()
                logger.info("✅ 向量数据清空完成")
            except Exception as e:
                logger.warning(f"⚠️  清空向量数据失败: {str(e)}")
            
            # 5. 分批插入商单数据到向量数据库
            logger.info(f"📥 开始插入 {min(len(orders), max_orders)} 个商单到向量数据库...")
            
            inserted_count = 0
            failed_count = 0
            
            # 根据环境设置不同的批次大小
            testing_env = os.getenv('TESTING', 'false').lower()
            if testing_env in ['true', '1', 'yes']:
                batch_size = 20  # 测试环境：小批次
                logger.info("🔧 测试环境：批次大小20")
            else:
                batch_size = 100  # 生产环境：大批次
                logger.info("🚀 生产环境：批次大小100")
            
            for i in range(0, min(len(orders), max_orders), batch_size):
                batch_orders = orders[i:i + batch_size]
                logger.info(f"   处理批次 {i//batch_size + 1}: {len(batch_orders)} 个商单")
                
                # 批量插入当前批次
                try:
                    self.milvus_db.add_orders(batch_orders)
                    inserted_count += len(batch_orders)
                    logger.info(f"   批次 {i//batch_size + 1} 批量插入成功: {len(batch_orders)} 个商单")
                except Exception as e:
                    failed_count += len(batch_orders)
                    logger.error(f"   批次 {i//batch_size + 1} 批量插入失败: {str(e)}")
                
                # 批次间短暂休息，避免过载
                if i + batch_size < min(len(orders), max_orders):
                    time.sleep(0.1)
            
            processing_time = time.time() - start_time
            
            # 5. 输出初始化结果
            logger.info(f"🎉 向量数据库初始化完成!")
            logger.info(f"   总商单数: {len(orders)}")
            logger.info(f"   成功插入: {inserted_count}")
            logger.info(f"   插入失败: {failed_count}")
            logger.info(f"   处理时间: {processing_time:.2f}秒")
            
            return {
                "success": inserted_count > 0,
                "orders_count": len(orders),
                "inserted_count": inserted_count,
                "failed_count": failed_count,
                "processing_time": processing_time,
                "success_rate": (inserted_count / len(orders)) * 100 if orders else 0
            }
            
        except Exception as e:
            logger.error(f"❌ 向量数据库初始化失败: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "orders_count": 0,
                "inserted_count": 0,
                "processing_time": 0
            }
    
    def _convert_to_vector_format(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        将商单数据转换为向量数据库格式
        
        Args:
            order: 原始商单数据
            
        Returns:
            Dict: 向量数据库格式的商单数据
        """
        try:
            # 确保必要字段存在
            order_vector = {
                'id': str(order.get('id', '')),
                'taskNumber': order.get('taskNumber', ''),
                'userId': str(order.get('userId', '')),
                'industryName': order.get('industryName', ''),
                'title': order.get('title', ''),
                'content': order.get('content', ''),
                'fullAmount': float(order.get('fullAmount', 0)),
                'state': order.get('state', ''),
                'createTime': order.get('createTime', ''),
                'updateTime': order.get('updateTime', ''),
                'siteId': str(order.get('siteId', ''))
            }
            
            # 验证必要字段
            required_fields = ['id', 'taskNumber', 'userId', 'title']
            missing_fields = [field for field in required_fields if not order_vector[field]]
            
            if missing_fields:
                logger.warning(f"商单 {order.get('taskNumber', order.get('id'))} 缺少必要字段: {missing_fields}")
            
            return order_vector
            
        except Exception as e:
            logger.error(f"转换商单格式失败: {str(e)}")
            # 返回默认格式
            return {
                'id': str(order.get('id', '')),
                'taskNumber': order.get('taskNumber', ''),
                'userId': str(order.get('userId', '')),
                'industryName': order.get('industryName', ''),
                'title': order.get('title', ''),
                'content': order.get('content', ''),
                'fullAmount': 0.0,
                'state': order.get('state', ''),
                'createTime': order.get('createTime', ''),
                'updateTime': order.get('updateTime', ''),
                'siteId': str(order.get('siteId', ''))
            }
    
    def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            bool: 是否健康
        """
        try:
            # 检查后端API连接
            backend_healthy = self.backend_client.health_check()
            
            # 检查Milvus连接
            milvus_healthy = self.milvus_db.collection is not None
            
            return backend_healthy and milvus_healthy
            
        except Exception as e:
            logger.error(f"健康检查失败: {str(e)}")
            return False
    
    def get_statistics(self) -> Dict[str, Any]:
        """
        获取统计信息
        
        Returns:
            Dict: 统计信息
        """
        try:
            stats = {
                "backend_healthy": self.backend_client.health_check(),
                "milvus_healthy": self.milvus_db.collection is not None,
                "collection_name": self.milvus_db.collection.name if self.milvus_db.collection else None,
                "total_entities": 0
            }
            
            # 获取集合中的实体数量
            if self.milvus_db.collection:
                try:
                    stats["total_entities"] = self.milvus_db.collection.num_entities
                except Exception as e:
                    logger.warning(f"获取实体数量失败: {str(e)}")
                    stats["total_entities"] = "unknown"
            
            return stats
            
        except Exception as e:
            logger.error(f"获取统计信息失败: {str(e)}")
            return {"error": str(e)}

# 创建全局实例
vector_db_initializer = VectorDBInitializer()

def get_vector_db_initializer() -> VectorDBInitializer:
    """获取向量数据库初始化器实例"""
    return vector_db_initializer 