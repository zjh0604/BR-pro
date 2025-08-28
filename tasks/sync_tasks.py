from celery import Task
from celery_app import app
from services.backend_sync_service import BackendSyncService
import logging
import time
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class SyncTask(Task):
    """同步任务基类"""
    def on_success(self, retval, task_id, args, kwargs):
        """任务成功时的回调"""
        info = f'Sync task {task_id} succeeded with result: {retval}'
        logger.info(info)
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """任务失败时的回调"""
        info = f'Sync task {task_id} failed with exception: {exc}'
        logger.error(info)

@app.task(base=SyncTask, name='tasks.sync_all_orders', bind=True)
def sync_all_orders(self):
    """
    全量同步商单数据任务
    
    Returns:
        bool: 是否同步成功
    """
    try:
        logger.info("开始执行全量同步任务...")
        
        sync_service = BackendSyncService()
        success = sync_service.sync_all_orders()
        
        if success:
            logger.info("全量同步任务完成")
        else:
            logger.error("全量同步任务失败")
        
        return success
        
    except Exception as e:
        logger.error(f"全量同步任务异常: {str(e)}")
        return False

@app.task(base=SyncTask, name='tasks.sync_order_events', bind=True)
def sync_order_events(self):
    """
    增量同步商单事件任务
    
    Returns:
        bool: 是否同步成功
    """
    try:
        logger.info("开始执行事件同步任务...")
        
        sync_service = BackendSyncService()
        success = sync_service.sync_order_events()
        
        if success:
            logger.info("事件同步任务完成")
        else:
            logger.error("事件同步任务失败")
        
        return success
        
    except Exception as e:
        logger.error(f"事件同步任务异常: {str(e)}")
        return False

@app.task(base=SyncTask, name='tasks.rolling_calculation', bind=True)
def rolling_calculation(self):
    """
    滚动计算推荐任务 - 智能增量更新版本
    
    根据事件库更新情况，智能更新受影响用户的推荐池
    """
    try:
        logger.info("开始执行滚动计算任务...")
        
        sync_service = BackendSyncService()
        
        # 获取最新事件信息
        latest_info = sync_service.api_client.get_latest_event_info()
        latest_event_id = latest_info.get("latest_event_id", 0)
        event_count = latest_info.get("event_count", 0)
        
        # 获取当前同步状态
        sync_status = sync_service.get_sync_status()
        last_event_id = sync_status.get("last_event_id", 0)
        
        # 检查是否有新事件
        if latest_event_id > last_event_id and event_count > 0:
            logger.info(f"发现新事件，触发滚动计算: 当前事件ID={last_event_id}, 最新事件ID={latest_event_id}")
            
            # 先同步事件
            events_success = sync_service.sync_order_events()
            if not events_success:
                logger.error("事件同步失败，跳过滚动计算")
                return False
            
            # 获取受影响的用户列表（基于事件类型和商单ID）
            affected_users = _get_affected_users_from_events(latest_event_id, last_event_id)
            
            if affected_users:
                logger.info(f"发现 {len(affected_users)} 个受影响的用户，开始智能更新推荐池")
                
                # 为每个受影响用户触发异步推荐池重新生成
                success_count = 0
                for user_id in affected_users:
                    try:
                        # 延迟导入异步任务模块
                        from tasks.recommendation_tasks import enhanced_preload_pagination_pool
                        
                        if enhanced_preload_pagination_pool:
                            task_result = enhanced_preload_pagination_pool.delay(user_id, pool_size=150)
                            logger.info(f"✅ 为用户 {user_id} 触发推荐池重新生成任务: task_id={task_result.id}")
                            success_count += 1
                        else:
                            logger.warning(f"⚠️ 用户 {user_id} 的异步任务模块不可用")
                            
                    except Exception as e:
                        logger.warning(f"⚠️ 为用户 {user_id} 触发异步任务失败: {str(e)}")
                
                logger.info(f"滚动计算完成：成功触发 {success_count}/{len(affected_users)} 个用户的推荐池重新生成")
                
                # 清除相关用户的推荐缓存，确保下次请求使用新生成的推荐池
                try:
                    cache_service = sync_service.cache_service
                    for user_id in affected_users:
                        cache_service.invalidate_user_cache(user_id)
                    logger.info(f"已清除 {len(affected_users)} 个用户的推荐缓存")
                except Exception as e:
                    logger.warning(f"清除用户推荐缓存失败: {str(e)}")
                
                return True
            else:
                logger.info("没有发现受影响的用户，跳过推荐池更新")
                return True
        else:
            logger.info("没有新事件，跳过滚动计算")
            return True
            
    except Exception as e:
        logger.error(f"滚动计算任务异常: {str(e)}")
        return False

def _get_affected_users_from_events(latest_event_id: int, last_event_id: int) -> List[str]:
    """
    根据事件ID范围获取受影响的用户列表
    
    实现逻辑：
    1. 获取事件ID范围内的所有事件
    2. 分析事件类型（插入/删除/更新）
    3. 对于插入操作：将新增商单向量化，找出相似商单，识别受影响用户
    4. 对于删除操作：通过Redis反向映射找到受影响用户，清理相关缓存
    5. 返回受影响用户列表
    
    Args:
        latest_event_id: 最新事件ID
        last_event_id: 上次处理的事件ID
        
    Returns:
        List[str]: 受影响的用户ID列表
    """
    try:
        # 使用推荐更新服务来获取受影响用户
        from services.recommendation_update_service import get_recommendation_update_service
        update_service = get_recommendation_update_service()
        
        # 获取事件ID范围内的事件数据
        from services.backend_sync_service import BackendSyncService
        sync_service = BackendSyncService()
        
        # 获取事件数据（这里需要根据实际的事件库接口来实现）
        events = sync_service.get_events_in_range(last_event_id, latest_event_id)
        
        if not events:
            logger.info(f"事件ID范围 {last_event_id}-{latest_event_id} 内没有事件")
            return []
        
        logger.info(f"获取到 {len(events)} 个事件，开始分析受影响用户")
        
        # 分别处理不同类型的事件
        affected_users = set()
        
        for event in events:
            event_type = _analyze_event_type(event)
            
            if event_type == "insert":
                # 插入操作：分析新增商单对哪些用户有影响
                insert_affected = update_service.get_affected_users_from_events([event])
                affected_users.update(insert_affected)
                logger.info(f"插入事件 {event.get('id')} 影响 {len(insert_affected)} 个用户")
                
            elif event_type == "delete":
                # 删除操作：通过Redis反向映射找到受影响用户
                delete_affected = _handle_order_delete_event(event)
                affected_users.update(delete_affected)
                logger.info(f"删除事件 {event.get('id')} 影响 {len(delete_affected)} 个用户")
                
            elif event_type == "update":
                # 更新操作：可能需要重新计算推荐
                update_affected = update_service.get_affected_users_from_events([event])
                affected_users.update(update_affected)
                logger.info(f"更新事件 {event.get('id')} 影响 {len(update_affected)} 个用户")
        
        logger.info(f"从事件中识别出 {len(affected_users)} 个受影响用户")
        return list(affected_users)
        
    except Exception as e:
        logger.error(f"获取受影响用户失败: {str(e)}")
        return []

def _analyze_event_type(event: Dict[str, Any]) -> str:
    """
    分析事件类型
    
    Args:
        event: 事件数据
        
    Returns:
        str: 事件类型 (insert/delete/update)
    """
    try:
        old_state = event.get('oldState')
        new_state = event.get('newState')
        
        if new_state == 'WaitReceive' and old_state != 'WaitReceive':
            return "insert"  # 状态变为WaitReceive，表示新商单
        elif old_state == 'WaitReceive' and new_state != 'WaitReceive':
            return "delete"  # 状态从WaitReceive变为其他，表示商单被删除或状态改变
        else:
            return "update"  # 其他状态变化
            
    except Exception as e:
        logger.warning(f"分析事件类型失败: {str(e)}")
        return "update"

def _handle_order_delete_event(event: Dict[str, Any]) -> List[str]:
    """
    处理商单删除事件，复用orders接口中的删除逻辑
    
    Args:
        event: 删除事件数据
        
    Returns:
        List[str]: 受影响的用户ID列表
    """
    try:
        order_id = event.get('id')
        if not order_id:
            logger.warning("删除事件缺少商单ID")
            return []
        
        # 复用orders接口中的删除逻辑
        from services.cache_service import get_cache_service
        from business_milvus_db import BusinessMilvusDB
        
        cache_service = get_cache_service()
        vector_db = BusinessMilvusDB()
        
        # 1. 通过Redis反向映射找到受影响用户
        affected_users = cache_service.get_order_affected_users(str(order_id))
        
        if affected_users:
            logger.info(f"商单 {order_id} 删除影响 {len(affected_users)} 个用户")
            
            # 2. 从所有用户的推荐列表中删除该商单
            for user_id in affected_users:
                try:
                    cache_service.remove_order_from_all_recommendations(user_id, str(order_id))
                    logger.info(f"已从用户 {user_id} 推荐列表中删除商单 {order_id}")
                except Exception as e:
                    logger.warning(f"从用户 {user_id} 推荐列表中删除商单 {order_id} 失败: {str(e)}")
            
            # 3. 从向量数据库中删除商单
            try:
                vector_db.remove_order(str(order_id))
                logger.info(f"已从向量数据库中删除商单 {order_id}")
            except Exception as e:
                logger.warning(f"从向量数据库中删除商单 {order_id} 失败: {str(e)}")
            
            # 4. 清理Redis中的反向映射
            try:
                cache_service.remove_order_from_all_recommendations(str(order_id), "")
                logger.info(f"已清理商单 {order_id} 的反向映射")
            except Exception as e:
                logger.warning(f"清理商单 {order_id} 反向映射失败: {str(e)}")
        
        return affected_users
        
    except Exception as e:
        logger.error(f"处理商单删除事件失败: {str(e)}")
        return []

@app.task(base=SyncTask, name='tasks.health_check', bind=True)
def health_check(self):
    """
    健康检查任务
    
    检查后端服务可用性和数据同步状态
    """
    try:
        logger.info("开始执行健康检查任务...")
        
        sync_service = BackendSyncService()
        
        # 检查后端服务可用性
        backend_healthy = sync_service.api_client.health_check()
        if not backend_healthy:
            logger.error("后端服务不可用")
            return False
        
        # 获取同步状态
        sync_status = sync_service.get_sync_status()
        last_sync_time = sync_status.get("last_sync_time")
        total_orders = sync_status.get("total_orders", 0)
        
        logger.info(f"健康检查完成: 后端服务正常, 最后同步时间={last_sync_time}, 商单总数={total_orders}")
        return True
        
    except Exception as e:
        logger.error(f"健康检查任务异常: {str(e)}")
        return False

# 定时任务配置
def schedule_sync_tasks():
    """配置定时同步任务"""
    from celery.schedules import crontab
    
    # 每天凌晨2点执行全量同步
    app.conf.beat_schedule = {
        'sync-all-orders-daily': {
            'task': 'tasks.sync_all_orders',
            'schedule': crontab(hour=2, minute=0),
        },
        'sync-order-events-every-5-minutes': {
            'task': 'tasks.sync_order_events',
            'schedule': crontab(minute='*/5'),
        },
        'rolling-calculation-every-10-minutes': {
            'task': 'tasks.rolling_calculation',
            'schedule': crontab(minute='*/10'),
        },
        'health-check-every-hour': {
            'task': 'tasks.health_check',
            'schedule': crontab(minute=0),
        },
    } 