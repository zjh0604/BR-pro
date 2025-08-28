from celery import Task
from celery_app import app
from typing import List, Dict, Any
import logging
import traceback
import time
import signal 

logger = logging.getLogger(__name__)

# 延迟导入服务模块，避免循环依赖
def _get_recommendation_service():
    """延迟获取推荐服务实例"""
    try:
        # 确保在Celery Worker环境中能正确导入
        import sys
        import os
        
        # 添加项目根目录到Python路径
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        from services.recommend_service import get_recommendation_service
        service = get_recommendation_service()
        if service is None:
            logger.error("推荐服务实例为None")
            return None
        return service
    except ImportError as e:
        logger.error(f"无法导入推荐服务: {e}")
        return None
    except Exception as e:
        logger.error(f"获取推荐服务异常: {e}")
        return None

def _get_cache_service():
    """延迟获取缓存服务实例"""
    try:
        # 确保在Celery Worker环境中能正确导入
        import sys
        import os
        
        # 添加项目根目录到Python路径
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        from services.cache_service import get_cache_service
        service = get_cache_service()
        if service is None:
            logger.error("缓存服务实例为None")
            return None
        return service
    except ImportError as e:
        logger.error(f"无法导入缓存服务: {e}")
        return None
    except Exception as e:
        logger.error(f"获取缓存服务异常: {e}")
        return None

class CallbackTask(Task):
    """带回调的任务基类"""
    def on_success(self, retval, task_id, args, kwargs):
        """任务成功时的回调"""
        info = f'Task {task_id} succeeded with result: {retval}'
        logger.info(info)
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """任务失败时的回调"""
        info = f'Task {task_id} failed with exception: {exc}'
        logger.error(info)

# 已移除LLM分析任务
# @app.task(base=CallbackTask, bind=True, name='tasks.analyze_recommendations_with_llm', 
#           autoretry_for=(Exception,), retry_kwargs={'max_retries': 3, 'countdown': 60},
#           soft_time_limit=300, time_limit=360)  # 5分钟软限制，6分钟硬限制
# def analyze_recommendations_with_llm(self, user_id: str, initial_recommendations: List[Dict[str, Any]], user_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
#     """
#     异步执行LLM分析任务
#     
#     Args:
#         user_id: 用户ID
#         initial_recommendations: 初步推荐结果（基于向量相似度）
#         user_orders: 用户历史订单
#         
#     Returns:
#         Dict: 包含分析后的精准推荐结果
#     """
#     try:
#         # 获取服务实例
#         recommendation_service = _get_recommendation_service()
#         cache_service = _get_cache_service()
#         
#         # 更新任务状态为处理中
#         cache_service.set_task_status(user_id, self.request.id, "processing")
#         
#         logger.info(f"开始LLM分析任务: user_id={user_id}, task_id={self.request.id}, retry={self.request.retries}")
#         logger.info(f"初步推荐数量: {len(initial_recommendations)}")
#         
#         # 检查输入数据有效性
#         if not initial_recommendations:
#             logger.warning("初步推荐结果为空，使用降级策略")
#             final_recommendations = []
#         elif not user_orders:
#             logger.warning("用户订单为空，使用降级策略")
#             final_recommendations = initial_recommendations[:5]
#         else:
#             # 执行LLM分析
#             final_recommendations = []
#             
#             # 获取最近的用户订单用于分析
#             recent_order = user_orders[-1] if user_orders else None
#             
#             if recent_order and recent_order.get('corresponding_role'):
#                 role = recent_order.get('corresponding_role')
#                 logger.info(f"使用角色 '{role}' 进行LLM分析")
#                 
#                 try:
#                     # Linux/生产环境版本: 使用signal实现超时控制
#                     handler = lambda signum, frame: (_ for _ in ()).throw(TimeoutError("LLM分析超时"))
#                     signal.signal(signal.SIGALRM, handler)
#                     signal.alarm(40)  # 40秒超时
#                     try:
#                          scored_orders = recommendation_service._analyze_with_llm(role, initial_recommendations)
#                     finally:
#                           signal.alarm(0) # 关闭闹钟
# 
#                     # Windows兼容版本: 直接调用，无超时控制
#                     #logger.info(f"开始调用LLM分析，角色: {role}")
#                     #scored_orders = recommendation_service._analyze_with_llm(role, initial_recommendations)
#                     
#                     # 按分数排序并取前5个
#                     if scored_orders:
#                         scored_orders.sort(key=lambda x: x[1], reverse=True)
#                         final_recommendations = [order for order, score in scored_orders[:5]]
#                         logger.info(f"LLM分析完成，精准推荐数量: {len(final_recommendations)}")
#                     else:
#                         # LLM分析无结果，使用降级策略
#                         final_recommendations = initial_recommendations[:5]
#                         logger.warning("LLM分析无结果，使用降级策略")
#                         
#                 except TimeoutError:
#                     logger.error("LLM分析超时，使用降级策略")
#                     final_recommendations = initial_recommendations[:5]
#                 except Exception as llm_error:
#                     logger.error(f"LLM分析出错: {str(llm_error)}，使用降级策略")
#                     final_recommendations = initial_recommendations[:5]
#             else:
#                 # 如果没有角色信息，直接使用初步推荐结果
#                 final_recommendations = initial_recommendations[:5]
#                 logger.warning("没有角色信息，使用降级策略")
#         
#         # 确保最终推荐不为空
#         if not final_recommendations and initial_recommendations:
#             final_recommendations = initial_recommendations[:5]
#             logger.warning("使用初步推荐作为最终结果")
#         
#         # 保存精准推荐结果到缓存
#         if final_recommendations:
#             cache_service.set_final_recommendations(user_id, final_recommendations)
#             
#             # 建立反向映射，用于增量更新
#             try:
#                 cache_service.set_recommendation_with_reverse_mapping(user_id, final_recommendations)
#                 logger.info(f"LLM分析结果反向映射已建立: user_id={user_id}, orders_count={len(final_recommendations)}")
#             except Exception as e:
#                 logger.warning(f"建立LLM分析结果反向映射失败: {str(e)}")
#         
#         # 更新任务状态为完成
#         result = {
#             "status": "completed",
#             "user_id": user_id,
#             "recommendations_count": len(final_recommendations),
#             "recommendations": final_recommendations,
#             "retry_count": self.request.retries,
#             "processing_time": time.time()
#         }
#         cache_service.set_task_status(user_id, self.request.id, "completed", result)
#         
#         logger.info(f"LLM分析任务完成: user_id={user_id}, task_id={self.request.id}, retries={self.request.retries}")
#         
#         return result
#         
#     except Exception as e:
#         logger.error(f"LLM分析任务失败: {str(e)}")
#         logger.error(traceback.format_exc())
#         
#         # 获取缓存服务
#         cache_service = _get_cache_service()
#         
#         # 如果是最后一次重试，使用降级策略
#         if self.request.retries >= 2:  # 最大重试3次
#             logger.error("达到最大重试次数，使用降级策略")
#             
#             # 使用初步推荐作为最终结果
#             fallback_recommendations = initial_recommendations[:5] if initial_recommendations else []
#             if fallback_recommendations:
#                 cache_service.set_final_recommendations(user_id, fallback_recommendations)
#             
#             # 更新任务状态为完成（降级成功）
#             fallback_result = {
#                 "status": "completed_with_fallback",
#                 "user_id": user_id,
#                 "recommendations_count": len(fallback_recommendations),
#                 "recommendations": fallback_recommendations,
#                 "error": str(e),
#                 "retry_count": self.request.retries
#             }
#             cache_service.set_task_status(user_id, self.request.id, "completed_with_fallback", fallback_result)
#             
#             return fallback_result
#         else:
#             # 更新任务状态为失败，将会触发重试
#             error_result = {
#                 "status": "failed",
#                 "error": str(e),
#                 "user_id": user_id,
#                 "retry_count": self.request.retries
#             }
#             cache_service.set_task_status(user_id, self.request.id, "failed", error_result)
#             
#             # 抛出异常触发重试
#             raise

@app.task(name='tasks.cleanup_user_cache')
def cleanup_user_cache(user_id: str) -> bool:
    """
    清理用户缓存的异步任务
    
    Args:
        user_id: 用户ID
        
    Returns:
        bool: 是否清理成功
    """
    try:
        cache_service = _get_cache_service()
        result = cache_service.invalidate_user_cache(user_id)
        logger.info(f"清理用户缓存任务完成: user_id={user_id}, result={result}")
        return result
    except Exception as e:
        logger.error(f"清理用户缓存失败: {str(e)}")
        return False

@app.task(name='tasks.preload_pagination_pool', bind=True)
def preload_pagination_pool(self, user_id: str, pool_size: int = 100) -> Dict[str, Any]:
    """
    预生成分页推荐池的异步任务
    
    Args:
        user_id: 用户ID
        pool_size: 推荐池大小
        
    Returns:
        Dict: 预生成结果
    """
    try:
        logger.info(f"开始预生成推荐池: user_id={user_id}, pool_size={pool_size}, task_id={self.request.id}")
        
        # 获取服务实例
        recommendation_service = _get_recommendation_service()
        cache_service = _get_cache_service()
        
        start_time = time.time()
        
        # 生成大量推荐池
        large_recommendations = recommendation_service._get_large_recommendations(user_id, n_results=pool_size)
        
        generation_time = time.time() - start_time
        
        if large_recommendations:
            # 缓存推荐池（1小时有效期）
            cache_key = f"paginated_recommendations_{user_id}"
            cache_success = cache_service.cache_data(cache_key, large_recommendations, expire_time=3600)
            
            logger.info(f"推荐池预生成完成: user_id={user_id}, 生成{len(large_recommendations)}条推荐, "
                       f"耗时{generation_time:.2f}秒, 缓存结果={cache_success}")
            
            # 同时为无限滚动准备缓存
            scroll_cache = {
                "recommendations": large_recommendations,
                "last_refresh": time.time(),
                "seen_ids": []
            }
            scroll_cache_key = f"infinite_scroll_{user_id}"
            cache_service.cache_data(scroll_cache_key, scroll_cache, expire_time=7200)
            
            result = {
                "status": "success",
                "user_id": user_id,
                "pool_size": len(large_recommendations),
                "generation_time": generation_time,
                "cache_key": cache_key,
                "message": "推荐池预生成成功"
            }
        else:
            logger.warning(f"推荐池预生成失败: user_id={user_id}, 无推荐内容")
            result = {
                "status": "empty",
                "user_id": user_id,
                "pool_size": 0,
                "generation_time": generation_time,
                "message": "无可推荐内容"
            }
        
        return result
        
    except Exception as e:
        logger.error(f"推荐池预生成任务失败: user_id={user_id}, error={str(e)}")
        logger.error(traceback.format_exc())
        
        return {
            "status": "failed",
            "user_id": user_id,
            "error": str(e),
            "message": "推荐池预生成失败"
        }

@app.task(name='tasks.enhanced_preload_pagination_pool', bind=True)
def enhanced_preload_pagination_pool(self, user_id: str, pool_size: int = 150) -> Dict[str, Any]:
    """
    增强版推荐池预生成任务 - 使用优化的多策略生成
    
    策略优化:
    1. 向量相似度推荐 (50%)
    3. 热门商单推荐 (40%)
    4. 随机多样性推荐 (10%)

    
    Args:
        user_id: 用户ID
        pool_size: 推荐池大小
        
    Returns:
        Dict: 预生成结果详情
    """
    try:
        logger.info(f"开始增强版推荐池预生成: user_id={user_id}, pool_size={pool_size}")
        
        recommendation_service = _get_recommendation_service()
        cache_service = _get_cache_service()
        
        start_time = time.time()
        
        # 获取用户历史订单
        user_orders = recommendation_service._get_user_orders_from_backend(user_id)
        
        if not user_orders:
            logger.warning(f"用户 {user_id} 无历史订单，使用多策略冷启动推荐")
            # 用户无历史，使用多策略冷启动推荐
            recommendations = _generate_cold_start_recommendations(recommendation_service, user_id, pool_size)
        else:
            # 多策略组合生成推荐池
            all_recommendations = []
            
            # 策略1: 向量相似度推荐 (50%)
            similarity_count = int(pool_size * 0.5)
            for order in user_orders[-2:]:  # 使用最近2个订单
                similar_orders = recommendation_service.vector_db.find_similar_orders(order, n_results=similarity_count//2)
                similar_orders = [o for o in similar_orders if o.get('user_id') != user_id]
                similar_orders = recommendation_service._filter_available_orders(similar_orders)
                all_recommendations.extend(similar_orders)
            
            # 策略2: 用户角色上下游推荐 (25%) - 优化：基于角色关系而非分类
            # 暂时注释：角色信息不传递，后续可能启用
            # role_relationship_count = int(pool_size * 0.25)
            # user_role = user_orders[-1].get('corresponding_role') if user_orders else None
            # if user_role and user_role != 'N/A':
            #     role_orders = _get_role_relationship_recommendations(
            #         recommendation_service, user_role, user_id, role_relationship_count
            #     )
            #     all_recommendations.extend(role_orders)
            # else:
            #     # 如果没有角色信息，使用热门推荐补充
            #     popular_orders = recommendation_service._get_popular_orders(user_id, n_results=role_relationship_count)
            #     all_recommendations.extend(popular_orders)
            
            # 临时策略：使用热门推荐补充
            role_relationship_count = int(pool_size * 0.25)
            popular_orders = recommendation_service._get_popular_orders(user_id, n_results=role_relationship_count)
            all_recommendations.extend(popular_orders)
            
            # 策略3: 热门商单推荐 (15%)
            popular_count = int(pool_size * 0.15)
            popular_orders = recommendation_service._get_popular_orders(user_id, n_results=popular_count)
            all_recommendations.extend(popular_orders)
            
            # 策略4: 随机多样性推荐 (10%)
            random_count = int(pool_size * 0.1)
            random_orders = recommendation_service._get_random_available_orders(
                user_id, exclude_count=0, n_results=random_count
            )
            all_recommendations.extend(random_orders)
            
            # 去重并保持多样性
            recommendations = recommendation_service._deduplicate_recommendations(all_recommendations)
        
        generation_time = time.time() - start_time
        
        if recommendations:
            # 限制最终推荐池大小
            recommendations = recommendations[:pool_size]
            
            # 缓存推荐池
            cache_key = f"paginated_recommendations_{user_id}"
            cache_service.cache_data(cache_key, recommendations, expire_time=3600)
            
            # 同时准备无限滚动缓存
            scroll_cache = {
                "recommendations": recommendations,
                "last_refresh": time.time(),
                "seen_ids": []
            }
            scroll_cache_key = f"infinite_scroll_{user_id}"
            cache_service.cache_data(scroll_cache_key, scroll_cache, expire_time=7200)
            
            # 建立反向映射，用于增量更新
            try:
                cache_service.set_recommendation_with_reverse_mapping(user_id, recommendations)
                logger.info(f"异步推荐池反向映射已建立: user_id={user_id}, orders_count={len(recommendations)}")
            except Exception as e:
                logger.warning(f"建立异步推荐池反向映射失败: {str(e)}")
            
            logger.info(f"增强版推荐池预生成完成: user_id={user_id}, 生成{len(recommendations)}条推荐, 耗时{generation_time:.2f}秒")
            
            result = {
                "status": "success",
                "user_id": user_id,
                "pool_size": len(recommendations),
                "generation_time": generation_time,
                "cache_keys": [cache_key, scroll_cache_key],
                "strategies_used": ["similarity", "role_relationship", "popular", "random"],
                "message": "增强版推荐池预生成成功"
            }
        else:
            result = {
                "status": "empty",
                "user_id": user_id,
                "pool_size": 0,
                "generation_time": generation_time,
                "message": "无可推荐内容"
            }
        
        return result
        
    except Exception as e:
        logger.error(f"增强版推荐池预生成失败: user_id={user_id}, error={str(e)}")
        logger.error(traceback.format_exc())
        
        return {
            "status": "failed",
            "user_id": user_id,
            "error": str(e),
            "message": "增强版推荐池预生成失败"
        }

# 暂时注释：角色信息不传递，后续可能启用
# def _get_role_relationship_recommendations(recommendation_service, user_role: str, user_id: str, n_results: int) -> List[Dict[str, Any]]:
#     """
#     基于用户角色上下游关系获取推荐商单
#     
#     Args:
#         recommendation_service: 推荐服务实例
#         user_role: 用户角色
#         user_id: 用户ID
#         n_results: 推荐数量
#         
#     Returns:
#         List[Dict]: 推荐商单列表
#     """
#     try:
#         # 获取用户角色的图关系网络
#         role_id = recommendation_service.graph_db.get_role_id_by_name(user_role)
#         if not role_id:
#             logger.warning(f"未找到角色 {user_role} 的图关系")
#             return []
#         
#         # 获取1-2跳的关联角色
#         related_roles = _get_related_roles_with_depth(recommendation_service.graph_db, role_id, depth=2)
#         if not related_roles:
#             logger.warning(f"角色 {user_role} 无关联角色")
#             return []
#         
#         # 基于关联角色获取商单
#         from storage.db import SessionLocal
#         from models.order import Order
#         
#         db = SessionLocal()
#         try:
#             related_role_names = [role["role_name"] for role in related_roles]
#         
#             # 分批查询关联角色的商单
#             batch_size = 10
#             all_orders = []
#             
#             for i in range(0, len(related_role_names), batch_size):
#                 batch_roles = related_role_names[i:i + batch_size]
#         
#                 orders_obj = db.query(Order).filter(
#                     Order.corresponding_role.in_(batch_roles),
#                     Order.user_id != user_id,
#                     Order.is_deleted == False,
#                     Order.status == "pending"
#                 ).limit(15).all()  # 每批限制15个
#         
#                 batch_orders = [recommendation_service._order_to_dict(order) for order in orders_obj]
#                 all_orders.extend(batch_orders)
#         
#         # 为推荐结果添加策略标识
#         for order in all_orders:
#             order["recommendation_strategy"] = "role_relationship"
#             order["strategy_weight"] = 0.25
#         
#         logger.info(f"基于角色关系找到 {len(all_orders)} 个推荐")
#         return all_orders[:n_results]
#         
#     finally:
#         db.close()
#         
#     except Exception as e:
#         logger.error(f"获取角色关系推荐失败: {str(e)}")
#         return []

# 暂时注释：角色信息不传递，后续可能启用
# def _get_related_roles_with_depth(graph_db, role_id: str, depth: int = 2) -> List[Dict[str, Any]]:
#     """
#     获取指定深度的关联角色
#     
#     Args:
#         graph_db: 图数据库实例
#         role_id: 角色ID
#         depth: 深度
#         
#     Returns:
#         List[Dict]: 关联角色列表
#     """
#     try:
#         related_roles = []
#         
#         # 获取1跳关联角色
#         if depth >= 1:
#             direct_relations = graph_db.get_related_roles(role_id, relation_type="upstream")
#             related_roles.extend(direct_relations)
#             
#             direct_relations_down = graph_db.get_related_roles(role_id, relation_type="downstream")
#             related_relations_down)
#         
#         # 获取2跳关联角色
#         if depth >= 2:
#             for relation in direct_relations[:5]:  # 限制数量避免过多查询
#                 second_level = graph_db.get_related_roles(relation["role_id"], relation_type="upstream")
#                 related_roles.extend(second_level)
#                 
#                 second_level_down = graph_db.get_related_roles(relation["role_id"], relation_type="downstream")
#                 related_roles.extend(second_level_down)
        
        # 去重
        unique_roles = []
        seen_role_ids = set()
        for role in related_roles:
            if role["role_id"] not in seen_role_ids:
                seen_role_ids.add(role["role_id"])
                unique_roles.append(role)
        
        return unique_roles
        
    except Exception as e:
        logger.error(f"获取关联角色失败: {str(e)}")
        return []

def _generate_cold_start_recommendations(recommendation_service, user_id: str, pool_size: int) -> List[Dict[str, Any]]:
    """
    为无历史用户生成冷启动推荐池（避免与首页推荐重复）
    
    优化策略：
    1. 平台商单（去重后）- 避免与首页推荐重复
    2. 热门商单（按创建时间）- 替代时效性推荐
    3. 随机多样性商单
    
    Args:
        recommendation_service: 推荐服务实例
        user_id: 用户ID
        pool_size: 推荐池大小
        
    Returns:
        List[Dict]: 推荐商单列表
    """
    try:
        logger.info(f"开始生成无历史用户推荐池: user_id={user_id}, pool_size={pool_size}")
        
        # 简化冷启动推荐逻辑，不再依赖角色信息
        logger.info(f"使用简化冷启动推荐策略")
        
        # 构建去重集合（暂时为空，后续可以基于用户行为数据）
        homepage_order_ids = set()
        
        all_recommendations = []
        
        # 策略1: 平台商单（去重后）- 30% - 暂时注释：平台商单逻辑不使用
        # platform_count = int(pool_size * 0.3)
        # platform_orders = _get_platform_orders_with_deduplication(
        #     recommendation_service, user_id, platform_count, homepage_order_ids
        # )
        # all_recommendations.extend(platform_orders)
        # logger.info(f"平台商单（去重后）: {len(platform_orders)} 个")
        
        # 策略2: 热门商单（按创建时间）- 70% - 调整比例
        popular_count = int(pool_size * 0.7)
        popular_orders = _get_popular_orders_with_deduplication(
            recommendation_service, user_id, popular_count, homepage_order_ids
        )
        all_recommendations.extend(popular_orders)
        logger.info(f"热门商单（去重后）: {len(popular_orders)} 个")
        
        # 策略3: 角色关联商单（如果用户有角色信息且去重后）- 20% - 暂时注释：角色关联逻辑不使用
        # if user_role and user_role != 'N/A':
        #     role_count = int(pool_size * 0.2)
        #     role_orders = _get_role_orders_with_deduplication(
        #         recommendation_service, user_role, user_id, role_count, homepage_order_ids
        #     )
        #     all_recommendations.extend(role_orders)
        #     logger.info(f"角色关联商单（去重后）: {len(role_orders)} 个")
        # else:
        #     # 如果没有角色信息，用热门商单补充
        #     additional_popular_count = int(pool_size * 0.2)
        #     additional_popular = _get_popular_orders_with_deduplication(
        #         recommendation_service, user_id, additional_popular_count, homepage_order_ids
        #     )
        #     all_recommendations.extend(additional_popular)
        #     logger.info(f"补充热门商单: {len(additional_popular)} 个")
        
        # 策略4: 随机多样性商单 - 30% - 调整比例
        random_count = int(pool_size * 0.3)
        random_orders = _get_random_orders_with_deduplication(
            recommendation_service, user_id, random_count, homepage_order_ids
        )
        all_recommendations.extend(random_orders)
        logger.info(f"随机多样性商单（去重后）: {len(random_orders)} 个")
        
        # 去重并保持多样性
        unique_recommendations = recommendation_service._deduplicate_recommendations(all_recommendations)
        
        logger.info(f"无历史用户推荐池生成完成: 目标{pool_size}条，实际{len(unique_recommendations)}条")
        return unique_recommendations[:pool_size]
        
    except Exception as e:
        logger.error(f"无历史用户推荐池生成失败: {str(e)}")
        # 降级到热门推荐
        return recommendation_service._get_popular_orders(user_id, n_results=pool_size)

def _get_platform_orders_with_deduplication(recommendation_service, user_id: str, n_results: int, 
                                           exclude_order_ids: set) -> List[Dict[str, Any]]:
    """获取平台商单（去重后）"""
    try:
        platform_orders = recommendation_service._get_platform_orders(n_results * 2)  # 多获取一些用于过滤
        
        # 去重过滤
        filtered_orders = []
        for order in platform_orders:
            order_id = f"{order.get('user_id')}_{order.get('order_id', order.get('wish_title'))}"
            if order_id not in exclude_order_ids:
                filtered_orders.append(order)
                if len(filtered_orders) >= n_results:
                    break
        
        # 为推荐结果添加策略标识
        for order in filtered_orders:
            order["recommendation_strategy"] = "platform_orders"
            order["strategy_weight"] = 0.3
        
        return filtered_orders
        
    except Exception as e:
        logger.error(f"获取平台商单（去重后）失败: {str(e)}")
        return []

def _get_popular_orders_with_deduplication(recommendation_service, user_id: str, n_results: int, 
                                          exclude_order_ids: set) -> List[Dict[str, Any]]:
    """获取热门商单（去重后）"""
    try:
        popular_orders = recommendation_service._get_popular_orders(user_id, n_results * 2)  # 多获取一些用于过滤
        
        # 去重过滤
        filtered_orders = []
        for order in popular_orders:
            order_id = f"{order.get('user_id')}_{order.get('order_id', order.get('wish_title'))}"
            if order_id not in exclude_order_ids:
                filtered_orders.append(order)
                if len(filtered_orders) >= n_results:
                    break
        
        # 为推荐结果添加策略标识
        for order in filtered_orders:
            order["recommendation_strategy"] = "popular_orders"
            order["strategy_weight"] = 0.4
        
        return filtered_orders
        
    except Exception as e:
        logger.error(f"获取热门商单（去重后）失败: {str(e)}")
        return []

# def _get_role_orders_with_deduplication(recommendation_service, user_role: str, user_id: str, n_results: int, 
#                                        exclude_order_ids: set) -> List[Dict[str, Any]]:
#     """获取角色关联商单（去重后）"""
#     try:
#         # 获取用户角色的图关系网络
#         role_id = recommendation_service.graph_db.get_role_id_by_name(user_role)
#         if not role_id:
#             logger.warning(f"未找到角色 {user_role} 的图关系")
#             return []
        
#         # 获取1-2跳的关联角色
#         related_roles = _get_related_roles_with_depth(recommendation_service.graph_db, role_id, depth=2)
#         if not related_roles:
#             logger.warning(f"角色 {user_role} 无关联角色")
#             return []
        
#         # 基于关联角色获取商单
#         from storage.db import SessionLocal
#         from models.order import Order
        
#         db = SessionLocal()
#         try:
#             related_role_names = [role["role_name"] for role in related_roles]
            
#             # 分批查询关联角色的商单
#             batch_size = 10
#             all_orders = []
            
#             for i in range(0, len(related_role_names), batch_size):
#                 batch_roles = related_role_names[i:i + batch_size]
                
#                 orders_obj = db.query(Order).filter(
#                     Order.corresponding_role.in_(batch_roles),
#                     Order.user_id != user_id,
#                     Order.is_deleted == False,
#                     Order.status == "pending"
#                 ).limit(20).all()  # 每批限制20个
                
#                 batch_orders = [recommendation_service._order_to_dict(order) for order in orders_obj]
#                 all_orders.extend(batch_orders)
            
#             # 去重过滤
#             filtered_orders = []
#             for order in all_orders:
#                 order_id = f"{order.get('user_id')}_{order.get('order_id', order.get('wish_title'))}"
#                 if order_id not in exclude_order_ids:
#                     filtered_orders.append(order)
#                     if len(filtered_orders) >= n_results:
#                         break
            
#             # 为推荐结果添加策略标识
#             for order in filtered_orders:
#                 order["recommendation_strategy"] = "role_relationship"
#                 order["strategy_weight"] = 0.2
            
#             return filtered_orders
            
#         finally:
#             db.close()
            
#     except Exception as e:
#         logger.error(f"获取角色关联商单（去重后）失败: {str(e)}")
#         return []

def _get_random_orders_with_deduplication(recommendation_service, user_id: str, n_results: int, 
                                         exclude_order_ids: set) -> List[Dict[str, Any]]:
    """获取随机多样性商单（去重后）"""
    try:
        random_orders = recommendation_service._get_random_available_orders(
            user_id, exclude_count=0, n_results=n_results * 2
        )
        
        # 去重过滤
        filtered_orders = []
        for order in random_orders:
            order_id = f"{order.get('user_id')}_{order.get('order_id', order.get('wish_title'))}"
            if order_id not in exclude_order_ids:
                filtered_orders.append(order)
                if len(filtered_orders) >= n_results:
                    break
        
        # 为推荐结果添加策略标识
        for order in filtered_orders:
            order["recommendation_strategy"] = "random_diversity"
            order["strategy_weight"] = 0.1
        
        return filtered_orders
        
    except Exception as e:
        logger.error(f"获取随机多样性商单（去重后）失败: {str(e)}")
        return [] 