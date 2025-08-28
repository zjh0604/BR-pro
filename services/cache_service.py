import redis
import json
import logging
import time
import hashlib
from typing import Dict, Any, List, Optional
from datetime import timedelta
import os

logger = logging.getLogger(__name__)

class CacheService:
    """Redis缓存服务，用于缓存推荐结果 - 优化版本：共享后端Redis"""
    
    def __init__(self):
        # 使用后端Redis服务配置
        self.redis_host = os.getenv('BACKEND_REDIS_HOST', 'localhost')
        self.redis_port = int(os.getenv('BACKEND_REDIS_PORT', 6379))
        self.redis_db = int(os.getenv('BACKEND_REDIS_DB', 10))  # 使用DB10-16分片
        self.redis_password = os.getenv('BACKEND_REDIS_PASSWORD', None)
        
        # 添加键前缀，避免与后端数据冲突
        self.key_prefix = "business_rec:"
        
        # 创建Redis连接池
        self.pool = redis.ConnectionPool(
            host=self.redis_host,
            port=self.redis_port,
            db=self.redis_db,
            password=self.redis_password,
            decode_responses=True,
            max_connections=20,  # 减少最大连接数，避免资源浪费
            retry_on_timeout=True,
            socket_keepalive=True,
            socket_connect_timeout=5,
            socket_timeout=10
        )
        self.redis_client = redis.Redis(connection_pool=self.pool)
        
        # 缓存过期时间设置
        self.initial_recommendation_ttl = timedelta(minutes=30)  # 初步推荐缓存30分钟
        self.final_recommendation_ttl = timedelta(hours=2)  # 精准推荐缓存2小时
        self.task_status_ttl = timedelta(minutes=10)  # 任务状态缓存10分钟
        
        # 缓存版本控制
        self.cache_version = "v2.0.0"  # 缓存版本号，用于缓存失效
        
        # 缓存键前缀映射
        self.key_prefixes = {
            "initial_rec": f"{self.key_prefix}rec:initial:{self.cache_version}",
            "final_rec": f"{self.key_prefix}rec:final:{self.cache_version}",
            "task_status": f"{self.key_prefix}task:{self.cache_version}",
            "user_profile": f"{self.key_prefix}user:profile:{self.cache_version}",
            "platform_orders": f"{self.key_prefix}platform:orders:{self.cache_version}",
            "cold_start": f"{self.key_prefix}cold:start:{self.cache_version}",
            "vector_cache": f"{self.key_prefix}vector:cache:{self.cache_version}",
            "graph_cache": f"{self.key_prefix}graph:cache:{self.cache_version}",
            "user_rec": f"{self.key_prefix}user_rec:{self.cache_version}",  # 用户推荐映射
            "order_users": f"{self.key_prefix}order_users:{self.cache_version}",  # 反向映射
            "order_rec": f"{self.key_prefix}order_rec:{self.cache_version}"  # 商单推荐缓存
        }
        
    def _get_key(self, prefix: str, user_id: str, suffix: str = "", params: Dict[str, Any] = None) -> str:
        """
        生成优化的Redis键名
        
        Args:
            prefix: 键前缀类型
            user_id: 用户ID
            suffix: 后缀
            params: 额外参数，用于生成参数哈希
            
        Returns:
            str: 格式化的键名
        """
        base_key = f"{self.key_prefixes.get(prefix, prefix)}:{user_id}"
        
        if suffix:
            base_key = f"{base_key}:{suffix}"
            
        if params:
            # 对参数进行哈希，避免键名过长
            param_str = json.dumps(params, sort_keys=True, ensure_ascii=False)
            param_hash = hashlib.md5(param_str.encode()).hexdigest()[:8]
            base_key = f"{base_key}:{param_hash}"
            
        return base_key
    
    def _get_cache_key_with_metadata(self, key: str, metadata: Dict[str, Any] = None) -> str:
        """
        生成包含元数据的缓存键
        
        Args:
            key: 基础键名
            metadata: 元数据信息
            
        Returns:
            str: 包含元数据的键名
        """
        if metadata:
            meta_str = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
            meta_hash = hashlib.md5(meta_str.encode()).hexdigest()[:6]
            return f"{key}:meta:{meta_hash}"
        return key
    
    def set_initial_recommendations(self, user_id: str, recommendations: List[Dict[str, Any]]) -> bool:
        """
        存储初步推荐结果（基于向量相似度的结果）
        
        Args:
            user_id: 用户ID
            recommendations: 初步推荐的商单列表
            
        Returns:
            bool: 是否存储成功
        """
        try:
            key = self._get_key("initial_rec", user_id)
            
            # 优化数据结构：只存储必要字段，减少内存占用
            optimized_recommendations = []
            for rec in recommendations:
                optimized_rec = {
                    "order_id": rec.get("order_id"),
                    "user_id": rec.get("user_id"),
                    "wish_title": rec.get("wish_title", "")[:100],  # 限制标题长度
                    "corresponding_role": rec.get("corresponding_role", ""),
                    "classification": rec.get("classification", ""),
                    "wish_details": rec.get("wish_details", "")[:200],  # 限制详情长度
                    "is_platform_order": rec.get("is_platform_order", False),
                    "priority": rec.get("priority", 0),
                    "created_at": rec.get("created_at")
                }
                optimized_recommendations.append(optimized_rec)
            
            # 添加缓存元数据
            cache_data = {
                "data": optimized_recommendations,
                "metadata": {
                    "cached_at": int(time.time()),
                    "count": len(optimized_recommendations),
                    "version": self.cache_version,
                    "type": "initial_recommendations"
                }
            }
            
            value = json.dumps(cache_data, ensure_ascii=False, separators=(',', ':'))
            self.redis_client.setex(key, self.initial_recommendation_ttl, value)
            logger.info(f"缓存初步推荐结果成功: user_id={user_id}, count={len(optimized_recommendations)}")
            return True
        except Exception as e:
            logger.error(f"缓存初步推荐结果失败: {str(e)}")
            return False
    
    def get_initial_recommendations(self, user_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        获取初步推荐结果
        
        Args:
            user_id: 用户ID
            
        Returns:
            List[Dict]: 推荐结果列表，如果不存在则返回None
        """
        try:
            key = self._get_key("initial_rec", user_id)
            value = self.redis_client.get(key)
            if value:
                cache_data = json.loads(value)
                # 检查缓存版本
                if cache_data.get("metadata", {}).get("version") == self.cache_version:
                    logger.info(f"获取初步推荐缓存成功: user_id={user_id}, count={len(cache_data['data'])}")
                    return cache_data["data"]
                else:
                    # 版本不匹配，删除旧缓存
                    self.redis_client.delete(key)
                    logger.info(f"缓存版本不匹配，已删除旧缓存: user_id={user_id}")
            return None
        except Exception as e:
            logger.error(f"获取初步推荐结果失败: {str(e)}")
            return None
    
    def set_final_recommendations(self, user_id: str, recommendations: List[Dict[str, Any]]) -> bool:
        """
        存储精准推荐结果（经过LLM分析后的结果）
        
        Args:
            user_id: 用户ID
            recommendations: 精准推荐的商单列表
            
        Returns:
            bool: 是否存储成功
        """
        try:
            key = self._get_key("final_rec", user_id)
            
            # 优化数据结构：存储完整信息，但添加压缩标记
            cache_data = {
                "data": recommendations,
                "metadata": {
                    "cached_at": int(time.time()),
                    "count": len(recommendations),
                    "version": self.cache_version,
                    "type": "final_recommendations",
                    "compressed": False  # 未来可扩展压缩功能
                }
            }
            
            value = json.dumps(cache_data, ensure_ascii=False, separators=(',', ':'))
            self.redis_client.setex(key, self.final_recommendation_ttl, value)
            logger.info(f"缓存精准推荐结果成功: user_id={user_id}, count={len(recommendations)}")
            return True
        except Exception as e:
            logger.error(f"缓存精准推荐结果失败: {str(e)}")
            return False
    
    def get_final_recommendations(self, user_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        获取精准推荐结果
        
        Args:
            user_id: 用户ID
            
        Returns:
            List[Dict]: 推荐结果列表，如果不存在则返回None
        """
        try:
            key = self._get_key("final_rec", user_id)
            value = self.redis_client.get(key)
            if value:
                cache_data = json.loads(value)
                # 检查缓存版本
                if cache_data.get("metadata", {}).get("version") == self.cache_version:
                    logger.info(f"获取精准推荐缓存成功: user_id={user_id}, count={len(cache_data['data'])}")
                    return cache_data["data"]
                else:
                    # 版本不匹配，删除旧缓存
                    self.redis_client.delete(key)
                    logger.info(f"缓存版本不匹配，已删除旧缓存: user_id={user_id}")
            return None
        except Exception as e:
            logger.error(f"获取精准推荐结果失败: {str(e)}")
            return None
    
    def set_task_status(self, user_id: str, task_id: str, status: str, result: Optional[Dict] = None) -> bool:
        """
        设置异步任务状态
        
        Args:
            user_id: 用户ID
            task_id: 任务ID
            status: 任务状态 (pending, processing, completed, failed)
            result: 任务结果（可选）
            
        Returns:
            bool: 是否设置成功
        """
        try:
            key = self._get_key("task_status", user_id, task_id)
            value = {
                "task_id": task_id,
                "status": status,
                "result": result,
                "updated_at": int(time.time())
            }
            self.redis_client.setex(key, self.task_status_ttl, json.dumps(value))
            logger.info(f"设置任务状态成功: user_id={user_id}, task_id={task_id}, status={status}")
            return True
        except Exception as e:
            logger.error(f"设置任务状态失败: {str(e)}")
            return False
    
    def get_task_status(self, user_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        """
        获取异步任务状态
        
        Args:
            user_id: 用户ID
            task_id: 任务ID
            
        Returns:
            Dict: 任务状态信息，如果不存在则返回None
        """
        try:
            key = self._get_key("task_status", user_id, task_id)
            value = self.redis_client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"获取任务状态失败: {str(e)}")
            return None
    
    def get_user_task_ids(self, user_id: str) -> List[str]:
        """
        获取用户所有正在执行的任务ID列表
        
        Args:
            user_id: 用户ID
            
        Returns:
            List[str]: 正在执行的任务ID列表
        """
        try:
            pattern = f"{self.key_prefixes['task_status']}:{user_id}:*"
            keys = self.redis_client.keys(pattern)
            task_ids = []
            
            for key in keys:
                task_status = self.redis_client.get(key)
                if task_status:
                    status_data = json.loads(task_status)
                    status = status_data.get("status")
                    if status in ["pending", "processing"]:
                        # 从key中提取task_id
                        task_id = key.split(":")[-1]
                        task_ids.append(task_id)
            
            logger.info(f"用户 {user_id} 有 {len(task_ids)} 个正在执行的任务")
            return task_ids
        except Exception as e:
            logger.error(f"获取用户任务列表失败: {str(e)}")
            return []
    
    def invalidate_user_cache(self, user_id: str) -> bool:
        """
        清除用户的所有缓存数据
        
        Args:
            user_id: 用户ID
            
        Returns:
            bool: 是否清除成功
        """
        try:
            # 清除初步推荐缓存
            initial_key = self._get_key("initial_rec", user_id)
            self.redis_client.delete(initial_key)
            
            # 清除精准推荐缓存
            final_key = self._get_key("final_rec", user_id)
            self.redis_client.delete(final_key)
            
            # 清除任务状态缓存
            task_pattern = f"{self.key_prefixes['task_status']}:{user_id}:*"
            task_keys = self.redis_client.keys(task_pattern)
            if task_keys:
                self.redis_client.delete(*task_keys)
            
            # 清除用户相关缓存
            user_pattern = f"{self.key_prefixes['user_profile']}:{user_id}:*"
            user_keys = self.redis_client.keys(user_pattern)
            if user_keys:
                self.redis_client.delete(*user_keys)
            
            # 清除推荐池缓存（新增）
            pool_key = f"paginated_recommendations_{user_id}"
            self.redis_client.delete(pool_key)
            
            # 清除无限滚动缓存（新增）
            scroll_key = f"infinite_scroll_{user_id}"
            self.redis_client.delete(scroll_key)
            
            # 清除已查看商单缓存（新增）
            viewed_key = f"viewed_orders_{user_id}"
            self.redis_client.delete(viewed_key)
            
            logger.info(f"清除用户缓存成功: user_id={user_id}")
            return True
        except Exception as e:
            logger.error(f"清除用户缓存失败: {str(e)}")
            return False
    
    def invalidate_all_user_cache(self) -> bool:
        """
        清除所有用户的缓存数据（用于平台商单更新时）
        
        Returns:
            bool: 是否清除成功
        """
        try:
            # 清除所有推荐缓存
            rec_pattern = f"{self.key_prefixes['initial_rec']}:*"
            rec_keys = self.redis_client.keys(rec_pattern)
            if rec_keys:
                self.redis_client.delete(*rec_keys)
            
            final_pattern = f"{self.key_prefixes['final_rec']}:*"
            final_keys = self.redis_client.keys(final_pattern)
            if final_keys:
                self.redis_client.delete(*final_keys)
            
            # 清除所有任务状态缓存
            task_pattern = f"{self.key_prefixes['task_status']}:*"
            task_keys = self.redis_client.keys(task_pattern)
            if task_keys:
                self.redis_client.delete(*task_keys)
            
            logger.info(f"清除所有用户缓存成功: 推荐缓存 {len(rec_keys) + len(final_keys)} 个, 任务缓存 {len(task_keys)} 个")
            return True
        except Exception as e:
            logger.error(f"清除所有用户缓存失败: {str(e)}")
            return False

    def _clear_pattern_keys(self, pattern: str) -> int:
        """
        根据模式清除缓存键
        
        Args:
            pattern: Redis key模式，如 "paginated_recommendations_*"
            
        Returns:
            int: 清除的键数量
        """
        try:
            # 添加缓存前缀
            full_pattern = f"{self.key_prefix}:{pattern}"
            keys = self.redis_client.keys(full_pattern)
            if keys:
                deleted_count = self.redis_client.delete(*keys)
                logger.info(f"根据模式清除缓存: pattern={pattern}, 清除数量={deleted_count}")
                return deleted_count
            return 0
        except Exception as e:
            logger.error(f"根据模式清除缓存失败: pattern={pattern}, error={str(e)}")
            return 0
    
    def set_platform_orders_cache(self, orders: List[Dict[str, Any]], ttl: int = 3600) -> bool:
        """
        缓存平台商单数据
        
        Args:
            orders: 平台商单列表
            ttl: 缓存时间（秒）
            
        Returns:
            bool: 是否缓存成功
        """
        try:
            key = self._get_key("platform_orders", "global")
            
            # 优化平台商单数据结构
            optimized_orders = []
            for order in orders:
                optimized_order = {
                    "order_id": order.get("order_id"),
                    "user_id": order.get("user_id"),
                    "wish_title": order.get("wish_title", "")[:100],
                    "corresponding_role": order.get("corresponding_role", ""),
                    "classification": order.get("classification", ""),
                    "wish_details": order.get("wish_details", "")[:200],
                    "priority": order.get("priority", 0),
                    "created_at": order.get("created_at"),
                    "is_platform_order": True
                }
                optimized_orders.append(optimized_order)
            
            cache_data = {
                "data": optimized_orders,
                "metadata": {
                    "cached_at": int(time.time()),
                    "count": len(optimized_orders),
                    "version": self.cache_version,
                    "type": "platform_orders"
                }
            }
            
            value = json.dumps(cache_data, ensure_ascii=False, separators=(',', ':'))
            self.redis_client.setex(key, ttl, value)
            logger.info(f"缓存平台商单成功: count={len(optimized_orders)}")
            return True
        except Exception as e:
            logger.error(f"缓存平台商单失败: {str(e)}")
            return False
    
    def get_platform_orders_cache(self) -> Optional[List[Dict[str, Any]]]:
        """
        获取缓存的平台商单数据
        
        Returns:
            List[Dict]: 平台商单列表，如果不存在则返回None
        """
        try:
            key = self._get_key("platform_orders", "global")
            value = self.redis_client.get(key)
            if value:
                cache_data = json.loads(value)
                if cache_data.get("metadata", {}).get("version") == self.cache_version:
                    logger.info(f"获取平台商单缓存成功: count={len(cache_data['data'])}")
                    return cache_data["data"]
                else:
                    self.redis_client.delete(key)
                    logger.info("平台商单缓存版本不匹配，已删除旧缓存")
            return None
        except Exception as e:
            logger.error(f"获取平台商单缓存失败: {str(e)}")
            return None
    
    def set_cold_start_cache(self, role: str, recommendations: List[Dict[str, Any]], ttl: int = 1800) -> bool:
        """
        缓存冷启动推荐数据
        
        Args:
            role: 用户角色
            recommendations: 冷启动推荐列表
            ttl: 缓存时间（秒）
            
        Returns:
            bool: 是否缓存成功
        """
        try:
            key = self._get_key("cold_start", role)
            
            cache_data = {
                "data": recommendations,
                "metadata": {
                    "cached_at": int(time.time()),
                    "count": len(recommendations),
                    "version": self.cache_version,
                    "type": "cold_start",
                    "role": role
                }
            }
            
            value = json.dumps(cache_data, ensure_ascii=False, separators=(',', ':'))
            self.redis_client.setex(key, ttl, value)
            logger.info(f"缓存冷启动推荐成功: role={role}, count={len(recommendations)}")
            return True
        except Exception as e:
            logger.error(f"缓存冷启动推荐失败: {str(e)}")
            return False
    
    def get_cold_start_cache(self, role: str) -> Optional[List[Dict[str, Any]]]:
        """
        获取缓存的冷启动推荐数据
        
        Args:
            role: 用户角色
            
        Returns:
            List[Dict]: 冷启动推荐列表，如果不存在则返回None
        """
        try:
            key = self._get_key("cold_start", role)
            value = self.redis_client.get(key)
            if value:
                cache_data = json.loads(value)
                if cache_data.get("metadata", {}).get("version") == self.cache_version:
                    logger.info(f"获取冷启动推荐缓存成功: role={role}, count={len(cache_data['data'])}")
                    return cache_data["data"]
                else:
                    self.redis_client.delete(key)
                    logger.info(f"冷启动推荐缓存版本不匹配，已删除旧缓存: role={role}")
            return None
        except Exception as e:
            logger.error(f"获取冷启动推荐缓存失败: {str(e)}")
            return None
    
    def get_cache_statistics(self) -> Dict[str, Any]:
        """
        获取缓存统计信息
        
        Returns:
            Dict: 缓存统计信息
        """
        try:
            stats = {}
            
            # 统计各类缓存的数量和大小
            for prefix_name, prefix in self.key_prefixes.items():
                pattern = f"{prefix}:*"
                keys = self.redis_client.keys(pattern)
                stats[prefix_name] = {
                    "count": len(keys),
                    "keys": keys[:10]  # 只显示前10个键作为示例
                }
            
            # 获取Redis内存使用情况
            info = self.redis_client.info("memory")
            stats["redis_memory"] = {
                "used_memory_human": info.get("used_memory_human"),
                "used_memory_peak_human": info.get("used_memory_peak_human"),
                "used_memory_rss_human": info.get("used_memory_rss_human")
            }
            
            return stats
        except Exception as e:
            logger.error(f"获取缓存统计信息失败: {str(e)}")
            return {}
    
    def adaptive_cache_ttl(self, key: str, access_count: int, base_ttl: int = 3600) -> int:
        """
        根据访问频率自适应调整缓存TTL
        
        Args:
            key: 缓存键
            access_count: 访问次数
            base_ttl: 基础TTL时间（秒）
            
        Returns:
            int: 调整后的TTL时间
        """
        try:
            # 根据访问频率调整TTL
            if access_count > 100:
                return base_ttl * 3  # 高频访问，延长TTL
            elif access_count > 50:
                return base_ttl * 2  # 中频访问，适度延长TTL
            elif access_count > 10:
                return base_ttl  # 低频访问，使用基础TTL
            else:
                return base_ttl // 2  # 很少访问，缩短TTL
        except Exception as e:
            logger.error(f"自适应TTL调整失败: {str(e)}")
            return base_ttl

    def ping(self) -> bool:
        """检查Redis连接是否正常"""
        try:
            return self.redis_client.ping()
        except Exception as e:
            logger.error(f"Redis连接检测失败: {str(e)}")
            return False

    def cache_data(self, key: str, data: Any, expire_time: int = 3600) -> bool:
        """
        通用缓存数据方法
        
        Args:
            key: 缓存键名
            data: 要缓存的数据
            expire_time: 过期时间（秒），默认1小时
        
        Returns:
            bool: 是否缓存成功
        """
        try:
            if isinstance(data, (dict, list)):
                value = json.dumps(data, ensure_ascii=False)
            else:
                value = str(data)
            
            self.redis_client.setex(key, expire_time, value)
            logger.debug(f"缓存数据成功: key={key}, expire_time={expire_time}s")
            return True
        except Exception as e:
            logger.error(f"缓存数据失败: key={key}, error={str(e)}")
            return False

    def get_cached_data(self, key: str) -> Optional[Any]:
        """
        通用获取缓存数据方法
        
        Args:
            key: 缓存键名
        
        Returns:
            Any: 缓存的数据，如果不存在则返回None
        """
        try:
            value = self.redis_client.get(key)
            if value:
                try:
                    # 尝试解析JSON
                    return json.loads(value)
                except json.JSONDecodeError:
                    # 如果不是JSON，直接返回字符串
                    return value
            return None
        except Exception as e:
            logger.error(f"获取缓存数据失败: key={key}, error={str(e)}")
            return None

    def delete_cache(self, key: str) -> bool:
        """
        删除指定的缓存
        
        Args:
            key: 缓存键名
        
        Returns:
            bool: 是否删除成功
        """
        try:
            result = self.redis_client.delete(key)
            logger.debug(f"删除缓存: key={key}, result={result}")
            return result > 0
        except Exception as e:
            logger.error(f"删除缓存失败: key={key}, error={str(e)}")
            return False

    def get_cache_ttl(self, key: str) -> int:
        """
        获取缓存的剩余过期时间
        
        Args:
            key: 缓存键名
        
        Returns:
            int: 剩余时间（秒），-1表示永不过期，-2表示键不存在
        """
        try:
            return self.redis_client.ttl(key)
        except Exception as e:
            logger.error(f"获取缓存TTL失败: key={key}, error={str(e)}")
            return -2

    def extend_cache_ttl(self, key: str, expire_time: int) -> bool:
        """
        延长缓存的过期时间
        
        Args:
            key: 缓存键名
            expire_time: 新的过期时间（秒）
        
        Returns:
            bool: 是否设置成功
        """
        try:
            result = self.redis_client.expire(key, expire_time)
            logger.debug(f"延长缓存TTL: key={key}, expire_time={expire_time}s, result={result}")
            return result
        except Exception as e:
            logger.error(f"延长缓存TTL失败: key={key}, error={str(e)}")
            return False

    def set_recommendation_with_reverse_mapping(self, user_id: str, recommendations: List[Dict[str, Any]]) -> bool:
        """
        设置推荐结果并建立反向映射（优化版本：确保ID一致性）
        
        Args:
            user_id: 用户ID
            recommendations: 推荐商单列表
            
        Returns:
            bool: 是否设置成功
        """
        try:
            # 1. 设置用户推荐列表
            user_key = f"{self.key_prefixes['user_rec']}:{user_id}"
            
            # 优先使用商单ID，如果没有则使用task_number作为备选
            order_ids = []
            for rec in recommendations:
                order_id = rec.get('id') or rec.get('order_id') or rec.get('backend_order_code')
                if order_id:
                    order_ids.append(str(order_id))
            
            if order_ids:
                self.redis_client.setex(user_key, 3600, json.dumps(order_ids))
                logger.info(f"设置用户推荐缓存: user_id={user_id}, orders={len(order_ids)}")
                
                # 2. 建立反向映射 order_id -> [user_ids]
                for order_id in order_ids:
                    reverse_key = f"{self.key_prefixes['order_users']}:{order_id}"
                    existing_users = self.redis_client.get(reverse_key)
                    
                    if existing_users:
                        user_list = json.loads(existing_users)
                        if user_id not in user_list:
                            user_list.append(user_id)
                            self.redis_client.setex(reverse_key, 3600, json.dumps(user_list))
                    else:
                        self.redis_client.setex(reverse_key, 3600, json.dumps([user_id]))
                
                logger.info(f"建立反向映射完成: user_id={user_id}, affected_orders={len(order_ids)}")
                return True
            else:
                logger.warning(f"用户 {user_id} 的推荐列表为空")
                return False
                
        except Exception as e:
            logger.error(f"设置推荐缓存失败: {str(e)}")
            return False
    
    def get_user_recommendations(self, user_id: str) -> Optional[List[str]]:
        """
        获取用户推荐列表
        
        Args:
            user_id: 用户ID
            
        Returns:
            List[str]: 商单编码列表，如果不存在则返回None
        """
        try:
            user_key = f"{self.key_prefixes['user_rec']}:{user_id}"
            result = self.redis_client.get(user_key)
            if result:
                return json.loads(result)
            return None
        except Exception as e:
            logger.error(f"获取用户推荐失败: {str(e)}")
            return None
    
    def get_order_affected_users(self, order_id: str) -> List[str]:
        """
        获取受影响的用户列表
        
        Args:
            order_id: 商单编码
            
        Returns:
            List[str]: 受影响用户ID列表
        """
        try:
            reverse_key = f"{self.key_prefixes['order_users']}:{order_id}"
            result = self.redis_client.get(reverse_key)
            if result:
                return json.loads(result)
            return []
        except Exception as e:
            logger.error(f"获取受影响用户失败: {str(e)}")
            return []
    
    def remove_order_from_all_recommendations(self, order_id: str) -> bool:
        """
        从所有用户推荐中移除指定商单
        
        Args:
            order_id: 商单编码
            
        Returns:
            bool: 是否移除成功
        """
        try:
            # 1. 获取包含该商单的所有用户
            affected_users = self.get_order_affected_users(order_id)
            
            if not affected_users:
                logger.info(f"商单 {order_id} 不在任何用户推荐中")
                return True
            
            logger.info(f"商单 {order_id} 影响用户: {affected_users}")
            
            # 2. 从每个用户的推荐中移除该商单
            for user_id in affected_users:
                user_key = f"{self.key_prefixes['user_rec']}:{user_id}"
                user_recommendations = self.redis_client.get(user_key)
                
                if user_recommendations:
                    order_ids = json.loads(user_recommendations)
                    if order_id in order_ids:
                        order_ids.remove(order_id)
                        self.redis_client.setex(user_key, 3600, json.dumps(order_ids))
                        logger.info(f"从用户 {user_id} 推荐中移除商单 {order_id}")
            
            # 3. 删除反向映射
            reverse_key = f"{self.key_prefixes['order_users']}:{order_id}"
            self.redis_client.delete(reverse_key)
            
            logger.info(f"成功移除商单 {order_id} 的所有推荐")
            return True
            
        except Exception as e:
            logger.error(f"移除商单失败: {str(e)}")
            return False
    
    def remove_order_from_user_recommendations(self, user_id: str, order_id: str) -> bool:
        """
        从指定用户的推荐列表中移除商单
        
        Args:
            user_id: 用户ID
            order_id: 商单ID
            
        Returns:
            bool: 是否移除成功
        """
        try:
            user_key = f"{self.key_prefixes['user_rec']}:{user_id}"
            user_recommendations = self.redis_client.get(user_key)
            
            if user_recommendations:
                order_ids = json.loads(user_recommendations)
                if order_id in order_ids:
                    order_ids.remove(order_id)
                    self.redis_client.setex(user_key, 3600, json.dumps(order_ids))
                    logger.info(f"从用户 {user_id} 推荐中移除商单 {order_id}")
                    return True
                else:
                    logger.info(f"用户 {user_id} 推荐中不存在商单 {order_id}")
                    return True
            else:
                logger.info(f"用户 {user_id} 没有推荐缓存")
                return True
                
        except Exception as e:
            logger.error(f"从用户推荐中移除商单失败: {str(e)}")
            return False
    
    def clear_order_mapping(self, order_id: str) -> bool:
        """
        清理商单的反向映射关系
        
        Args:
            order_id: 商单ID
            
        Returns:
            bool: 是否清理成功
        """
        try:
            # 删除商单到用户的反向映射
            reverse_key = f"{self.key_prefixes['order_users']}:{order_id}"
            self.redis_client.delete(reverse_key)
            
            # 删除商单的推荐缓存（如果存在）
            order_rec_key = f"{self.key_prefixes['order_rec']}:{order_id}"
            self.redis_client.delete(order_rec_key)
            
            logger.info(f"成功清理商单 {order_id} 的映射关系")
            return True
            
        except Exception as e:
            logger.error(f"清理商单映射失败: {str(e)}")
            return False
    
    def clear_all_recommendations(self) -> bool:
        """
        清除所有推荐缓存
        
        Returns:
            bool: 是否清除成功
        """
        try:
            # 清除用户推荐映射
            user_pattern = f"{self.key_prefixes['user_rec']}:*"
            user_keys = self.redis_client.keys(user_pattern)
            if user_keys:
                self.redis_client.delete(*user_keys)
            
            # 清除反向映射
            order_pattern = f"{self.key_prefixes['order_users']}:*"
            order_keys = self.redis_client.keys(order_pattern)
            if order_keys:
                self.redis_client.delete(*order_keys)
            
            logger.info(f"清除所有推荐缓存: 用户映射 {len(user_keys)} 个, 反向映射 {len(order_keys)} 个")
            return True
            
        except Exception as e:
            logger.error(f"清除所有推荐缓存失败: {str(e)}")
            return False
    
    def get_cache_data(self, key: str) -> Any:
        """
        获取缓存数据
        
        Args:
            key: 缓存键
            
        Returns:
            缓存的数据
        """
        try:
            data = self.redis_client.get(key)
            if data:
                return json.loads(data)
            return None
        except Exception as e:
            logger.error(f"获取缓存数据失败: {str(e)}")
            return None
    
    def set_cache_data(self, key: str, data: Any, ttl: int = 3600) -> bool:
        """
        设置缓存数据
        
        Args:
            key: 缓存键
            data: 要缓存的数据
            ttl: 过期时间（秒），默认1小时
            
        Returns:
            bool: 是否设置成功
        """
        try:
            serialized_data = json.dumps(data, ensure_ascii=False)
            self.redis_client.setex(key, ttl, serialized_data)
            logger.info(f"成功设置缓存: {key}, TTL: {ttl}秒")
            return True
        except Exception as e:
            logger.error(f"设置缓存数据失败: {str(e)}")
            return False

# 创建单例实例
cache_service = CacheService()

def get_cache_service() -> CacheService:
    """获取缓存服务的单例实例"""
    return cache_service 