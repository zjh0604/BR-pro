from typing import List, Dict, Any, Optional, Tuple
import logging
import json
from sqlalchemy.orm import Session
from business_milvus_db import BusinessMilvusDB
# from business_graph_db import BusinessGraphDB  # 暂停图数据库
from business_db import get_business_orders_by_user, save_business_order
from models.order import Order
from storage.db import SessionLocal
from my_qianfan_llm import llm  # 恢复LLM精排（不依赖角色）
from services.field_normalizer import FieldNormalizer
from services.cache_service import get_cache_service
from services.backend_sync_service import BackendSyncService
import uuid
from datetime import datetime
import random

# 异步任务模块状态（延迟导入避免循环依赖）
ASYNC_TASKS_ENABLED = None  # 改为None，表示未初始化
enhanced_preload_pagination_pool = None

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _check_async_tasks_availability():
    """检查异步任务模块可用性（延迟检查）"""
    global ASYNC_TASKS_ENABLED, enhanced_preload_pagination_pool
    
    if ASYNC_TASKS_ENABLED is None:
        try:
            # 延迟导入异步任务模块
            from tasks.recommendation_tasks import enhanced_preload_pagination_pool
            ASYNC_TASKS_ENABLED = True
            logger.info("✅ 异步推荐任务模块已启用")
        except ImportError as e:
            ASYNC_TASKS_ENABLED = False
            enhanced_preload_pagination_pool = None
            logger.warning(f"⚠️ 异步推荐任务模块导入失败: {str(e)}")
        except Exception as e:
            ASYNC_TASKS_ENABLED = False
            enhanced_preload_pagination_pool = None
            logger.error(f"❌ 异步推荐任务模块检查异常: {str(e)}")
    
    return ASYNC_TASKS_ENABLED

# 记录异步任务模块状态
if _check_async_tasks_availability():
    logger.info("异步推荐任务模块已启用")
else:
    logger.warning("异步推荐任务模块未启用，将使用同步模式")

class RecommendationService:
    def __init__(self):
        """初始化推荐服务,加载必要的组件"""
        self.vector_db = BusinessMilvusDB()  # 使用Milvus替代ChromaDB
        # self.graph_db = BusinessGraphDB()  # 暂停图数据库
        self.cache_service = get_cache_service()
        self.backend_sync_service = BackendSyncService()  # 后端同步服务

    def _order_to_dict(self, order: Order) -> Dict[str, Any]:
        """将Order对象转换为字典（已更新为后端字段）"""
        order_dict = {
            "id": getattr(order, 'id', None),
            "taskNumber": getattr(order, 'taskNumber', None),
            "userId": getattr(order, 'userId', None),
            "industryName": getattr(order, 'industryName', None),
            "title": getattr(order, 'title', None),
            "content": getattr(order, 'content', None),
            "fullAmount": float(getattr(order, 'fullAmount', 0)) if getattr(order, 'fullAmount', None) else None,
            "state": getattr(order, 'state', None),
            "createTime": getattr(order, 'createTime', None),
            "updateTime": getattr(order, 'updateTime', None),
            "siteId": getattr(order, 'siteId', None),
            "priority": getattr(order, 'priority', 0)  # 优先级字段，默认值为0
        }
        return FieldNormalizer.normalize_order(order_dict)

    def _get_user_orders_from_backend(self, user_id: str) -> List[Dict[str, Any]]:
        try:
            return self.backend_sync_service.get_user_orders_from_backend(user_id)
        except Exception as e:
            logger.error(f"从后端获取用户商单失败: {str(e)}")
            return []

    def process_new_order(self, order: Dict[str, Any]) -> bool:
        """
        处理新的商单，实现完整的推荐流程：
        1. 商单向量化（不保存到向量数据库）
        2. 向量相似度检索
        3. 同城筛选
        4. 初步推荐返回
        5. 异步LLM精排和推荐池生成
        
        Args:
            order: 商单数据
            
        Returns:
            bool: 处理是否成功
        """
        try:
            logger.info(f"开始处理新商单: {order.get('id') or order.get('taskNumber')}")
            
            # 1. 验证和标准化商单数据
            normalized_order = FieldNormalizer.normalize_order(order)
            validation = FieldNormalizer.validate_order(normalized_order)
            if not validation["is_valid"]:
                logger.error(f"订单数据验证失败，缺少字段: {validation['missing_fields']}")
                return False

            # 2. 立即进行向量相似度检索（初步推荐）
            logger.info("开始向量相似度检索...")
            try:
                similar_orders = self.vector_db.find_similar_orders_with_filters(
                    normalized_order, n_results=30, filters={"state": "WaitReceive"}
                )
                logger.info(f"向量相似度检索完成，找到 {len(similar_orders)} 个相似商单")
            except Exception as e:
                logger.error(f"向量相似度检索失败: {str(e)}")
                similar_orders = []

            # 3. 同城筛选
            site_id = normalized_order.get('siteId')
            if site_id and similar_orders:
                logger.info(f"启用同城筛选，筛选siteId={site_id}的商单")
                site_filtered_orders = [order for order in similar_orders if order.get('siteId') == site_id]
                if site_filtered_orders:
                    similar_orders = site_filtered_orders
                    logger.info(f"同城筛选完成，筛选后商单数量: {len(similar_orders)}")
                else:
                    logger.warning(f"siteId={site_id}下无匹配商单")
                    similar_orders = []
            elif site_id:
                logger.info(f"启用同城筛选，但无推荐结果")

            # 4. 保存初步推荐到缓存
            user_id = normalized_order.get('userId')
            if user_id and similar_orders:
                try:
                    # 限制初步推荐数量，避免缓存过大
                    initial_recommendations = similar_orders[:20]
                    self.cache_service.set_initial_recommendations(user_id, initial_recommendations)
                    logger.info(f"初步推荐已保存到缓存: user_id={user_id}, 数量={len(initial_recommendations)}")
                except Exception as e:
                    logger.warning(f"保存初步推荐到缓存失败: {str(e)}")

            # 5. 触发异步推荐池生成任务（已移除LLM精排）
            if user_id and similar_orders:
                try:
                    if _check_async_tasks_availability():
                        from tasks.recommendation_tasks import enhanced_preload_pagination_pool
                        # 触发异步推荐池生成任务
                        task_result = enhanced_preload_pagination_pool.delay(user_id, pool_size=150)
                        logger.info(f"✅ 已触发异步推荐池生成任务: user_id={user_id}, task_id={task_result.id}")
                        
                        # 已移除LLM精排任务
                        # from tasks.recommendation_tasks import analyze_recommendations_with_llm
                        # llm_task_result = analyze_recommendations_with_llm.delay(
                        #     user_id, similar_orders[:10], []  # 用户历史订单暂时为空
                        # )
                        # logger.info(f"✅ 已触发异步LLM精排任务: user_id={user_id}, task_id={llm_task_result.id}")
                        logger.info("✅ LLM精排任务已移除，仅保留推荐池生成任务")
                        
                    else:
                        logger.warning("⚠️ 异步任务模块未启用，无法触发异步任务")
                        
                except Exception as e:
                    logger.warning(f"⚠️ 触发异步任务失败: {str(e)}")
                    # 异步任务失败不影响主流程
            else:
                logger.warning("用户ID或相似商单为空，跳过异步任务")

            # 6. 清理相关缓存
            try:
                self.cache_service.invalidate_user_cache(user_id)
                logger.info(f"用户缓存清理完成: user_id={user_id}")
            except Exception as e:
                logger.warning(f"清理用户缓存失败: {str(e)}")

            logger.info(f"新商单处理完成: {normalized_order.get('task_number') or normalized_order.get('id')}")
            return True
            
        except Exception as e:
            logger.error(f"处理新商单时出错: {str(e)}")
            return False

    # 已移除LLM精排方法
    # def _llm_rank(self, query_brief: str, candidates: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
    #     """调用LLM对候选商单进行精排，不依赖角色，仅基于文本相关性与质量说明。
    #     返回按模型评分排序后的 top_k 列表。
    #     """
    #     try:
    #         if not candidates:
    #             return []
    #         # 构造提示词：包含用户最近商单的标题/内容摘要与候选摘要
    #         candidate_texts = []
    #         for idx, c in enumerate(candidates[:20], start=1):
    #             title = c.get('wish_title', '')
    #             content = c.get('wish_details', '')
    #         candidate_texts.append(f"[{idx}] 标题:{title}\n内容:{content}")
    #         prompt = (
    #             "请根据以下用户需求摘要，评估候选商单与其的匹配度，从高到低排序，给出前{top_k}个编号。\n"
    #             f"用户需求摘要: {query_brief}\n"
    #             "候选商单列表:\n" + "\n\n".join(candidate_texts) +
    #             "\n输出格式: 以英文逗号分隔的编号，例如: 3,1,2"
    #         )
    #         order_indexes = llm.rank_indices(prompt, num_return=top_k)  # 约定的简单接口
    #         reranked = []
    #         for i in order_indexes:
    #             idx = int(i) - 1
    #             if 0 <= idx < len(candidates):
    #                 reranked.append(candidates[idx])
    #         if not reranked:
    #             return candidates[:top_k]
    #         return reranked[:top_k]
    #     except Exception as e:
    #         logger.warning(f"LLM精排失败，使用向量相似度结果: {str(e)}")
    #         return candidates[:top_k]

    def get_recommendations(self, user_id: str, n_results: int = 5) -> Dict[str, Any]:
        """
        获取用户的个性化推荐（精简版）：
        - 不使用角色增强/冷启动/图数据库
        - 仅基于向量相似度 + 置顶用户自己的最新商单
        """
        try:
            user_orders = self._get_user_orders_from_backend(user_id)
            for order in user_orders:
                for field in ['title', 'content', 'industryName', 'fullAmount']:
                    if field not in order:
                        order[field] = "N/A"

            if not user_orders:
                # 简单冷启动兜底：从向量库取最近/热门（这里用随机近似）
                pool = self.vector_db.get_orders_by_filters({"state": "WaitReceive"}, limit=100)
                random.shuffle(pool)
                return {"user_orders": [], "recommended_orders": pool[:n_results], "recommendation_type": "cold_start_simple"}

            recent_orders = user_orders[-1:]
            all_recommendations = []
            for order in recent_orders:
                similar_orders = self.vector_db.find_similar_orders_with_filters(
                    order, n_results=50, filters={"state": "WaitReceive"}
                )
                # 已移除LLM精排，直接使用向量相似度结果
                # query_brief = f"标题:{order.get('title','')} 内容:{order.get('content','')}"
                # ranked = self._llm_rank(query_brief, similar_orders, top_k=n_results)
                ranked = similar_orders[:n_results]  # 直接取前n_results个
                for o in ranked:
                    for f in ['title', 'content', 'industryName', 'fullAmount']:
                        if f not in o:
                            o[f] = "N/A"
                all_recommendations.extend(ranked)

            # 去重限量
            seen_ids = set()
            unique_recommendations = []
            for o in all_recommendations:
                oid = f"{o.get('userId')}_{o.get('taskNumber', o.get('id'))}"
                if oid not in seen_ids:
                    seen_ids.add(oid)
                    unique_recommendations.append(o)
                if len(unique_recommendations) >= n_results:
                    break

            # 用户自己的最新商单置顶
            user_own_orders_for_display = []
            latest_orders = user_orders[-2:] if len(user_orders) >= 2 else user_orders[-1:]
            for uo in latest_orders:
                uo_display = uo.copy()
                for f in ['title', 'content', 'industryName', 'fullAmount']:
                    if f not in uo_display:
                        uo_display[f] = "N/A"
                user_own_orders_for_display.append(uo_display)

            final = []
            final.extend(user_own_orders_for_display)
            remaining = max(0, n_results - len(final))
            final.extend(unique_recommendations[:remaining])
            final = final[:n_results]
            
            # 按优先级排序
            final = self._sort_by_priority(final)

            return {
                "user_orders": user_orders,
                "recommended_orders": final,
                "recommendation_type": "vector_only"
            }
        except Exception as e:
            logger.error(f"获取推荐时出错: {str(e)}")
            return {"user_orders": [], "recommended_orders": []}

    def get_recommendations_async(self, user_id: str, n_results: int = 5) -> Dict[str, Any]:
        """
        异步获取推荐（精简版）：
        - 只用向量相似度
        - 不做角色增强/冷启动/平台置顶
        """
        try:
            cache_service = get_cache_service()
            user_orders = self._get_user_orders_from_backend(user_id)
            for order in user_orders:
                for field in ['title', 'content', 'industryName', 'fullAmount']:
                    if field not in order:
                        order[field] = "N/A"

            final_recommendations = cache_service.get_final_recommendations(user_id)
            if final_recommendations:
                return {"user_orders": user_orders, "recommended_orders": final_recommendations[:n_results], "is_cached": True, "recommendation_type": "final"}

            initial_recommendations = cache_service.get_initial_recommendations(user_id)
            if initial_recommendations:
                return {"user_orders": user_orders, "recommended_orders": initial_recommendations[:n_results], "is_cached": True, "recommendation_type": "initial"}

            if not user_orders:
                pool = self.vector_db.get_orders_by_filters({"state": "WaitReceive"}, limit=100)
                random.shuffle(pool)
                final = pool[:n_results]
                cache_service.set_initial_recommendations(user_id, final)
                return {"user_orders": [], "recommended_orders": final, "is_cached": False, "recommendation_type": "cold_start_simple"}

            # 计算初始候选
            search_orders = user_orders[-3:] if len(user_orders) >= 3 else user_orders
            all_initial = []
            for order in search_orders:
                similar_orders = self.vector_db.find_similar_orders_with_filters(
                    order, n_results=50, filters={"state": "WaitReceive"}
                )
                all_initial.extend(similar_orders)

            # 已移除LLM精排，直接使用向量相似度结果
            # query_brief = f"标题:{search_orders[-1].get('wish_title','')} 内容:{search_orders[-1].get('wish_details','')}" if search_orders else ""
            # ranked = self._llm_rank(query_brief, all_initial, top_k=n_results)
            ranked = all_initial[:n_results]  # 直接取前n_results个

            # 用户自己的商单置顶
            user_own_orders_for_display = []
            latest_orders = user_orders[-2:] if len(user_orders) >= 2 else user_orders[-1:]
            for uo in latest_orders:
                uo_display = uo.copy()
                for f in ['title', 'content', 'industryName', 'fullAmount']:
                    if f not in uo_display:
                        uo_display[f] = "N/A"
                user_own_orders_for_display.append(uo_display)

            final = []
            final.extend(user_own_orders_for_display)
            remaining = max(0, n_results - len(final))
            final.extend(ranked[:remaining])
            final = final[:n_results]
            
            # 按优先级排序
            final = self._sort_by_priority(final)

            cache_service.set_initial_recommendations(user_id, final)
            task_id = None  # 暂不做二阶段异步精排
            return {"user_orders": user_orders, "recommended_orders": final, "task_id": task_id, "is_cached": False, "recommendation_type": "vector_only_initial"}
        except Exception as e:
            logger.error(f"异步获取推荐时出错: {str(e)}")
            return {"user_orders": [], "recommended_orders": [], "is_cached": False}

    def recommend_orders(self, user_id: str, page: int = 1, page_size: int = 10, 
                        industry_name: str = None, amount_min: float = None, 
                        amount_max: float = None, created_at_start: str = None, 
                        created_at_end: str = None, search: str = None, 
                        recommend_pool_id: str = None, site_id: str = None,
                        use_cache: bool = True, refresh_strategy: str = "append") -> Dict[str, Any]:
        """
        统一的推荐接口 - 支持分页和筛选（已更新为后端字段）
        
        Args:
            user_id: 用户ID
            page: 页码（从1开始）
            page_size: 每页大小
            industry_name: 行业名称筛选（对应后端industryName字段）
            amount_min: 最小金额筛选
            amount_max: 最大金额筛选
            created_at_start: 创建时间开始
            created_at_end: 创建时间结束
            search: 搜索关键词
            recommend_pool_id: 推荐池ID
            use_cache: 是否使用缓存
            refresh_strategy: 刷新策略（append/replace）
        
        Returns:
            推荐结果字典
        """
        try:
            logger.info(f"开始为用户 {user_id} 获取推荐，页码: {page}, 每页: {page_size}")
            
            # 构建筛选条件
            filters = {"state": "WaitReceive"}  # 只推荐可接单的商单
            
            if industry_name:
                filters["industryName"] = industry_name
            if amount_min is not None:
                filters["amount_min"] = amount_min
            if amount_max is not None:
                filters["amount_max"] = amount_max
            if created_at_start:
                filters["created_at_start"] = created_at_start
            if created_at_end:
                filters["created_at_end"] = created_at_end
            if site_id:
                filters["siteId"] = site_id
            
            # 获取用户历史商单
            user_orders = self._get_user_orders_from_backend(user_id)
            
            # 验证用户ID格式（简单验证）
            if not self._is_valid_user_id(user_id):
                logger.warning(f"无效的用户ID格式: {user_id}")
                return {
                    "orders": [],
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                    "error": "无效的用户ID格式",
                    "recommendation_type": "invalid_user"
                }
            
            # 如果使用缓存且不是强制刷新
            if use_cache and refresh_strategy != "replace":
                cached_recommendations = self.cache_service.get_final_recommendations(user_id)
                if cached_recommendations:
                    logger.info(f"使用缓存推荐结果，用户: {user_id}")
                    # 应用筛选和分页
                    filtered_results = self._apply_filters_and_pagination(
                        cached_recommendations, filters, page, page_size, search
                    )
                    return {
                        "orders": filtered_results,
                        "total": len(cached_recommendations),
                        "page": page,
                        "page_size": page_size,
                        "is_cached": True,
                        "recommendation_type": "cached"
                    }
            
            # 计算推荐结果
            if user_orders:
                # 基于用户历史商单的推荐
                search_orders = user_orders[-3:] if len(user_orders) >= 3 else user_orders
                all_candidates = []
                
                for order in search_orders:
                    similar_orders = self.vector_db.find_similar_orders_with_filters(
                        order, n_results=50, filters=filters
                    )
                    all_candidates.extend(similar_orders)
                
                # 去重
                unique_candidates = []
                seen_ids = set()
                for candidate in all_candidates:
                    candidate_id = candidate.get('id') or candidate.get('taskNumber')
                    if candidate_id and candidate_id not in seen_ids:
                        seen_ids.add(candidate_id)
                        unique_candidates.append(candidate)
                
                # 已移除LLM精排，直接使用向量相似度结果
                # if search_orders:
                #     query_brief = f"标题:{search_orders[-1].get('title','')} 内容:{search_orders[-1].get('content','')}"
                #     ranked_candidates = self._llm_rank(query_brief, unique_candidates, top_k=100)
                # else:
                #     ranked_candidates = unique_candidates
                ranked_candidates = unique_candidates  # 直接使用向量相似度结果
                
                # 用户自己的商单置顶
                user_own_orders = []
                latest_orders = user_orders[-2:] if len(user_orders) >= 2 else user_orders[-1:]
                for uo in latest_orders:
                    uo_display = uo.copy()
                    for field in ['title', 'content', 'industryName', 'fullAmount']:
                        if field not in uo_display:
                            uo_display[field] = "N/A"
                    user_own_orders.append(uo_display)
                
                final_results = []
                final_results.extend(user_own_orders)
                remaining = max(0, 100 - len(final_results))
                final_results.extend(ranked_candidates[:remaining])
                
            else:
                # 冷启动推荐
                logger.info(f"用户 {user_id} 无历史商单，使用冷启动推荐")
                cold_start_pool = self.vector_db.get_orders_by_filters(filters, limit=100)
                random.shuffle(cold_start_pool)
                final_results = cold_start_pool[:100]
            
            # 同城匹配逻辑：如果有siteId，确保所有推荐商单都是该siteId
            if site_id and final_results:
                logger.info(f"启用同城匹配，筛选siteId={site_id}的商单")
                site_filtered_results = [order for order in final_results if order.get('siteId') == site_id]
                if site_filtered_results:
                    final_results = site_filtered_results
                    logger.info(f"同城匹配完成，筛选后商单数量: {len(final_results)}")
                else:
                    logger.warning(f"siteId={site_id}下无匹配商单，返回空结果")
                    final_results = []
            elif site_id:
                logger.info(f"启用同城匹配，但无推荐结果，直接返回空")
                final_results = []
            
            # 应用筛选和分页
            filtered_results = self._apply_filters_and_pagination(
                final_results, filters, page, page_size, search
            )
            
            # 按优先级排序（priority字段，默认值为0）
            filtered_results = self._sort_by_priority(filtered_results)
            
            # 缓存结果
            if use_cache:
                self.cache_service.set_final_recommendations(user_id, final_results)
            
            logger.info(f"成功为用户 {user_id} 生成推荐，总数: {len(final_results)}, 当前页: {len(filtered_results)}")
            
            return {
                "orders": filtered_results,
                "total": len(final_results),
                "page": page,
                "page_size": page_size,
                "is_cached": False,
                "recommendation_type": "generated"
            }
            
        except Exception as e:
            logger.error(f"推荐接口出错: {str(e)}")
            return {
                "orders": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "error": str(e)
            }
    
    def _format_recommendation_response(self, orders: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        格式化推荐响应，只返回必要字段
        
        Args:
            orders: 原始商单列表
            
        Returns:
            Dict: 包含格式化订单和用户推荐映射的字典
        """
        formatted_orders = []
        user_recommendations = {}  # userId -> [id1, id2, ...]
        
        for order in orders:
            # 只返回必要字段
            formatted_order = {
                "id": order.get('id'),
                "taskNumber": order.get('taskNumber'),
                "title": order.get('title'),
                "industryName": order.get('industryName'),
                "fullAmount": order.get('fullAmount'),
                "state": order.get('state'),
                "createTime": order.get('createTime'),
                "siteId": order.get('siteId')
            }
            formatted_orders.append(formatted_order)
            
            # 构建用户推荐映射
            user_id = str(order.get('userId', ''))
            if user_id:
                if user_id not in user_recommendations:
                    user_recommendations[user_id] = []
                user_recommendations[user_id].append(order.get('id'))
        
        return {
            "orders": formatted_orders,
            "user_recommendations": user_recommendations  # 反向映射：userId -> [id1, id2, ...]
        }
    
    def _apply_filters_and_pagination(self, orders: List[Dict[str, Any]], 
                                    filters: Dict[str, Any], page: int, 
                                    page_size: int, search: str = None) -> List[Dict[str, Any]]:
        """
        应用筛选和分页
        
        Args:
            orders: 订单列表
            filters: 筛选条件
            page: 页码
            page_size: 每页大小
            search: 搜索关键词
        
        Returns:
            筛选和分页后的结果
        """
        try:
            # 应用搜索筛选
            if search:
                filtered = []
                search_lower = search.lower()
                for order in orders:
                    title = order.get('title', '').lower()
                    content = order.get('content', '').lower()
                    if search_lower in title or search_lower in content:
                        filtered.append(order)
                orders = filtered
            
            # 应用金额筛选
            if filters.get('amount_min') is not None:
                orders = [o for o in orders if o.get('fullAmount', 0) >= filters['amount_min']]
            if filters.get('amount_max') is not None:
                orders = [o for o in orders if o.get('fullAmount', 0) <= filters['amount_max']]
            
            # 应用分页
            start_idx = (page - 1) * page_size
            end_idx = start_idx + page_size
            paginated = orders[start_idx:end_idx]
            
            return paginated
            
        except Exception as e:
            logger.error(f"应用筛选和分页时出错: {str(e)}")
            return orders[:page_size]  # 返回默认分页结果
    
    def recommend_orders_new(self, user_id: str, page: int = 1, page_size: int = 10, 
                        industry_name: str = None, amount_min: float = None, 
                        amount_max: float = None, created_at_start: str = None, 
                        created_at_end: str = None, search: str = None, 
                        recommend_pool_id: str = None, site_id: str = None,
                        use_cache: bool = True, refresh_strategy: str = "append") -> Dict[str, Any]:
        """
        新的推荐接口 - 实现正确的异步流程
        
        执行流程：
        1. 快速返回向量相似度搜索结果
        2. 同时异步启动推荐池生成（已移除LLM精排）
        3. 支持siteId同城筛选
        4. 支持从推荐池中分页获取
        
        Args:
            user_id: 用户ID
            page: 页码（从1开始）
            page_size: 每页大小
            industry_name: 行业名称筛选（对应后端industryName字段）
            amount_min: 最小金额筛选
            amount_max: 最大金额筛选
            created_at_start: 创建时间开始
            created_at_end: 创建时间结束
            search: 搜索关键词
            recommend_pool_id: 推荐池ID
            site_id: 站点ID，用于同城匹配
            use_cache: 是否使用缓存
            refresh_strategy: 刷新策略（append/replace）
        
        Returns:
            推荐结果字典
        """
        try:
            logger.info(f"开始为用户 {user_id} 获取推荐，页码: {page}, 每页: {page_size}")
            
            # 验证用户ID格式
            if not self._is_valid_user_id(user_id):
                logger.warning(f"无效的用户ID格式: {user_id}")
                return {
                    "orders": [],
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                    "error": "无效的用户ID格式",
                    "recommendation_type": "invalid_user"
                }
            
            # 构建筛选条件
            filters = {"state": "WaitReceive"}  # 只推荐可接单的商单
            
            if industry_name:
                filters["industryName"] = industry_name
            if amount_min is not None:
                filters["amount_min"] = amount_min
            if amount_max is not None:
                filters["amount_max"] = amount_max
            if created_at_start:
                filters["created_at_start"] = created_at_start
            if created_at_end:
                filters["created_at_end"] = created_at_end
            if site_id:
                filters["siteId"] = site_id
            
            # 如果使用缓存且不是强制刷新，优先检查推荐池缓存
            if use_cache and refresh_strategy != "replace":
                # 优先检查推荐池缓存（用于分页）
                pool_key = f"paginated_recommendations_{user_id}"
                pool_cache = self.cache_service.get_cache_data(pool_key)
                
                if pool_cache and len(pool_cache) >= page_size:
                    logger.info(f"使用推荐池缓存，用户: {user_id}, 池大小: {len(pool_cache)}")
                    # 应用筛选和分页
                    filtered_results = self._apply_filters_and_pagination(
                        pool_cache, filters, page, page_size, search
                    )
                    formatted_response = self._format_recommendation_response(filtered_results)
                    return {
                        "orders": formatted_response["orders"],
                        "user_recommendations": formatted_response["user_recommendations"],
                        "total": len(pool_cache),
                        "page": page,
                        "page_size": page_size,
                        "is_cached": True,
                        "recommendation_type": "pool_cached"
                    }
                
                # 检查最终推荐缓存
                cached_recommendations = self.cache_service.get_final_recommendations(user_id)
                if cached_recommendations:
                    logger.info(f"使用缓存推荐结果，用户: {user_id}")
                    # 应用筛选和分页
                    filtered_results = self._apply_filters_and_pagination(
                        cached_recommendations, filters, page, page_size, search
                    )
                    formatted_response = self._format_recommendation_response(filtered_results)
                    return {
                        "orders": formatted_response["orders"],
                        "user_recommendations": formatted_response["user_recommendations"],
                        "total": len(cached_recommendations),
                        "page": page,
                        "page_size": page_size,
                        "is_cached": True,
                        "recommendation_type": "cached"
                    }
            
            # 获取用户历史商单
            user_orders = self._get_user_orders_from_backend(user_id)
            
            # 快速生成向量相似度推荐结果（立即返回）
            quick_results = self._generate_quick_recommendations(user_orders, filters, site_id, page_size)
            
            # 同时异步生成推荐池任务（延迟导入避免循环依赖）
            try:
                # 检查异步任务模块可用性
                if _check_async_tasks_availability():
                    # 延迟导入异步任务模块
                    try:
                        from tasks.recommendation_tasks import enhanced_preload_pagination_pool
                        # from tasks.recommendation_tasks import analyze_recommendations_with_llm  # 已移除LLM任务
                        
                        # 触发异步推荐池预生成任务
                        if enhanced_preload_pagination_pool:
                            task_result = enhanced_preload_pagination_pool.delay(user_id, pool_size=150)
                            logger.info(f"✅ 已触发异步推荐池预生成任务: user_id={user_id}, task_id={task_result.id}")
                        else:
                            logger.warning("⚠️ enhanced_preload_pagination_pool任务不可用")
                        
                        # 已移除LLM精排任务
                        # if user_orders and analyze_recommendations_with_llm:
                        #     # 获取初步推荐用于LLM精排
                        #     initial_recommendations = quick_results[:20] if quick_results else []
                        #     if initial_recommendations:
                        #         llm_task_result = analyze_recommendations_with_llm.delay(
                        #             user_id, initial_recommendations, user_orders
                        #         )
                        #         logger.info(f"✅ 已触发异步LLM精排任务: user_id={user_id}, task_id={llm_task_result.id}")
                        logger.info("✅ LLM精排任务已移除，仅保留推荐池生成任务")
                        
                    except ImportError as e:
                        logger.warning(f"⚠️ 导入异步任务模块失败: {str(e)}")
                    except Exception as e:
                        logger.warning(f"⚠️ 异步任务模块异常: {str(e)}")
                else:
                    logger.warning("⚠️ 异步任务模块未启用，使用同步模式")
            except Exception as e:
                logger.warning(f"⚠️ 触发异步任务失败: {str(e)}")
            
            # 应用筛选和分页到快速结果
            filtered_results = self._apply_filters_and_pagination(
                quick_results, filters, page, page_size, search
            )
            
            # 按优先级排序
            filtered_results = self._sort_by_priority(filtered_results)
            
            logger.info(f"成功为用户 {user_id} 生成快速推荐，总数: {len(quick_results)}, 当前页: {len(filtered_results)}")
            
            # 格式化推荐响应
            formatted_response = self._format_recommendation_response(filtered_results)
            
            return {
                "orders": formatted_response["orders"],
                "user_recommendations": formatted_response["user_recommendations"],
                "total": len(quick_results),
                "page": page,
                "page_size": page_size,
                "is_cached": False,
                "recommendation_type": "quick_generated"
            }
            
        except Exception as e:
            logger.error(f"推荐接口出错: {str(e)}")
            return {
                "orders": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "error": str(e)
            }

    def _is_valid_user_id(self, user_id: str) -> bool:
        """
        验证用户ID格式是否有效
        
        Args:
            user_id: 用户ID字符串
        
        Returns:
            bool: 是否有效
        """
        try:
            # 简单验证：用户ID应该是数字或有效的字符串格式
            if not user_id or user_id.strip() == "":
                return False
            
            # 检查是否包含明显的无效标识（仅拒绝明显的无效标识）
            if "invalid_user" in user_id.lower():
                return False
            
            # 检查长度（用户ID通常不会太长）
            if len(user_id) > 50:
                return False
            
            return True
        except Exception as e:
            logger.warning(f"用户ID验证异常: {str(e)}")
            return False
    
    def _generate_quick_recommendations(self, user_orders: List[Dict[str, Any]], 
                                      filters: Dict[str, Any], site_id: str, 
                                      page_size: int) -> List[Dict[str, Any]]:
        """
        快速生成向量相似度推荐结果（立即返回）
        
        Args:
            user_orders: 用户历史商单
            filters: 筛选条件
            site_id: 站点ID
            page_size: 页面大小
        
        Returns:
            List[Dict]: 快速推荐结果
        """
        try:
            logger.info(f"开始生成快速推荐，用户商单数: {len(user_orders) if user_orders else 0}")
            
            if user_orders:
                # 基于用户历史商单的快速推荐
                search_orders = user_orders[-2:] if len(user_orders) >= 2 else user_orders
                all_candidates = []
                
                for order in search_orders:
                    try:
                        similar_orders = self.vector_db.find_similar_orders_with_filters(
                            order, n_results=30, filters=filters
                        )
                        if similar_orders:
                            all_candidates.extend(similar_orders)
                            logger.info(f"商单 {order.get('id')} 找到 {len(similar_orders)} 个相似商单")
                        else:
                            logger.warning(f"商单 {order.get('id')} 未找到相似商单")
                    except Exception as e:
                        logger.error(f"查询商单 {order.get('id')} 相似商单失败: {str(e)}")
                        continue
                
                # 去重
                unique_candidates = []
                seen_ids = set()
                for candidate in all_candidates:
                    candidate_id = candidate.get('id') or candidate.get('taskNumber')
                    if candidate_id and candidate_id not in seen_ids:
                        seen_ids.add(candidate_id)
                        unique_candidates.append(candidate)
                
                logger.info(f"去重后候选商单数: {len(unique_candidates)}")
                
                # 用户自己的商单置顶
                user_own_orders = []
                latest_orders = user_orders[-2:] if len(user_orders) >= 2 else user_orders[-1:]
                for uo in latest_orders:
                    uo_display = uo.copy()
                    for field in ['title', 'content', 'industryName', 'fullAmount']:
                        if field not in uo_display:
                            uo_display[field] = "N/A"
                    user_own_orders.append(uo_display)
                
                final_results = []
                final_results.extend(user_own_orders)
                remaining = max(0, 50 - len(final_results))
                final_results.extend(unique_candidates[:remaining])
                
            else:
                # 冷启动推荐
                logger.info("用户无历史商单，使用冷启动推荐")
                try:
                    cold_start_pool = self.vector_db.get_orders_by_filters(filters, limit=50)
                    if cold_start_pool:
                        random.shuffle(cold_start_pool)
                        final_results = cold_start_pool[:50]
                        logger.info(f"冷启动推荐成功，获取到 {len(final_results)} 个商单")
                    else:
                        logger.warning("冷启动推荐失败，向量数据库无数据")
                        final_results = []
                except Exception as e:
                    logger.error(f"冷启动推荐异常: {str(e)}")
                    final_results = []
            
            # 同城匹配逻辑：如果有siteId，确保所有推荐商单都是该siteId
            if site_id and final_results:
                logger.info(f"启用同城匹配，筛选siteId={site_id}的商单")
                site_filtered_results = [order for order in final_results if order.get('siteId') == site_id]
                if site_filtered_results:
                    final_results = site_filtered_results
                    logger.info(f"同城匹配完成，筛选后商单数量: {len(final_results)}")
                else:
                    logger.warning(f"siteId={site_id}下无匹配商单，返回空结果")
                    final_results = []
            elif site_id:
                logger.info(f"启用同城匹配，但无推荐结果，直接返回空")
                final_results = []
            
            logger.info(f"快速推荐生成完成，最终结果数: {len(final_results)}")
            return final_results[:page_size * 3]  # 返回3页的数据量，支持快速分页
            
        except Exception as e:
            logger.error(f"快速推荐生成失败: {str(e)}")
            return []

    def _sort_by_priority(self, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        按优先级排序推荐结果
        
        Args:
            orders: 订单列表
        
        Returns:
            按优先级排序后的订单列表（高优先级在前）
        """
        try:
            # 按priority字段排序，默认值为0，高优先级在前
            sorted_orders = sorted(
                orders, 
                key=lambda x: x.get('priority', 0), 
                reverse=True
            )
            return sorted_orders
        except Exception as e:
            logger.warning(f"优先级排序失败，使用原始顺序: {str(e)}")
            return orders
    
    # 已移除用户角色方法（不再使用）
    # def _get_user_role(self, user_id: str) -> Optional[str]:
    #     """
    #     获取用户角色信息
    #     
    #     Args:
    #         user_id: 用户ID
    #         
    #     Returns:
    #         用户角色，如果没有则返回None
    #     """
    #     try:
    #         # 从用户历史订单中获取角色信息
    #         user_orders = self._get_user_orders_from_backend(user_id)
    #         if user_orders and len(user_orders) > 0:
    #         # 获取最新订单的角色
    #             latest_order = user_orders[-1]
    #             return latest_order.get('corresponding_role', 'N/A')
    #         return None
    #     except Exception as e:
    #         logger.warning(f"获取用户角色失败: {str(e)}")
    #         return None
    
    def _get_popular_orders(self, user_id: str, n_results: int = 50) -> List[Dict[str, Any]]:
        """
        获取热门商单（按创建时间排序）
        
        Args:
            user_id: 用户ID
            n_results: 返回数量
            
        Returns:
            热门商单列表
        """
        try:
            # 从向量数据库获取热门商单
            filters = {"state": "WaitReceive"}
            popular_orders = self.vector_db.get_orders_by_filters(filters, limit=n_results * 2)
            
            # 按创建时间排序，取最新的
            if popular_orders:
                # 过滤掉用户自己的商单
                filtered_orders = [order for order in popular_orders if order.get('userId') != user_id]
                # 按创建时间排序（假设createTime是时间字符串）
                sorted_orders = sorted(filtered_orders, key=lambda x: x.get('createTime', ''), reverse=True)
                return sorted_orders[:n_results]
            
            return []
        except Exception as e:
            logger.error(f"获取热门商单失败: {str(e)}")
            return []
    
    def _get_random_available_orders(self, user_id: str, exclude_count: int = 0, n_results: int = 20) -> List[Dict[str, Any]]:
        """
        获取随机可用商单
        
        Args:
            user_id: 用户ID
            exclude_count: 排除数量
            n_results: 返回数量
            
        Returns:
            随机商单列表
        """
        try:
            # 从向量数据库获取随机商单
            filters = {"state": "WaitReceive"}
            available_orders = self.vector_db.get_orders_by_filters(filters, limit=n_results * 3)
            
            if available_orders:
                # 过滤掉用户自己的商单
                filtered_orders = [order for order in available_orders if order.get('userId') != user_id]
                # 随机打乱
                import random
                random.shuffle(filtered_orders)
                return filtered_orders[:n_results]
            
            return []
        except Exception as e:
            logger.error(f"获取随机商单失败: {str(e)}")
            return []
    
    def _deduplicate_recommendations(self, recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        去重推荐结果
        
        Args:
            recommendations: 推荐列表
            
        Returns:
            去重后的推荐列表
        """
        try:
            seen_ids = set()
            unique_recommendations = []
            
            for rec in recommendations:
                rec_id = rec.get('id') or rec.get('taskNumber')
                if rec_id and rec_id not in seen_ids:
                    seen_ids.add(rec_id)
                    unique_recommendations.append(rec)
            
            return unique_recommendations
        except Exception as e:
            logger.error(f"去重推荐结果失败: {str(e)}")
            return recommendations

    def _filter_promotional_orders(self, orders: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        筛选推广商单和正常商单
        
        Args:
            orders: 原始推荐池中的商单列表
            
        Returns:
            Tuple[List[Dict], List[Dict]]: (正常商单列表, 推广商单列表)
        """
        try:
            normal_orders = []
            promotional_orders = []
            
            for order in orders:
                # 检查promotion字段，默认为False
                if order.get('promotion', False):
                    promotional_orders.append(order)
                else:
                    normal_orders.append(order)
            
            logger.info(f"推广筛选完成: 正常商单 {len(normal_orders)} 个, 推广商单 {len(promotional_orders)} 个")
            return normal_orders, promotional_orders
            
        except Exception as e:
            logger.error(f"推广筛选失败: {str(e)}")
            # 出错时返回原始列表作为正常商单，推广商单为空
            return orders, []

    def _split_recommendation_pools(self, orders: List[Dict[str, Any]], user_id: str) -> Dict[str, Any]:
        """
        将推荐池分离为正常推荐池和推广商单池（优化版）
        
        优化逻辑：
        1. 当筛选后没有推广商单时，直接从向量数据库随机筛选推广商单
        2. 确保推广商单池始终有数据，提高覆盖率
        
        Args:
            orders: 原始推荐池中的商单列表
            user_id: 用户ID
            
        Returns:
            Dict: 包含双池数据的字典
        """
        try:
            # 筛选推广商单和正常商单
            normal_orders, promotional_orders = self._filter_promotional_orders(orders)
            
            # 存储到缓存中
            normal_pool_key = f"normal_recommendations_{user_id}"
            promotional_pool_key = f"promotional_recommendations_{user_id}"
            
            # 缓存正常推荐池（保持原有逻辑不变）
            self.cache_service.set_cache_data(normal_pool_key, normal_orders)
            
            # 优化推广商单池：如果筛选后没有推广商单，从向量数据库补充
            if not promotional_orders:
                logger.info(f"推广池为空，从向量数据库补充推广商单...")
                promotional_orders = self._get_promotional_orders_fallback(user_id)
                if promotional_orders:
                    logger.info(f"成功补充推广商单: {len(promotional_orders)} 个")
                else:
                    logger.warning(f"无法从向量数据库获取推广商单")
            
            # 缓存推广商单池
            self.cache_service.set_cache_data(promotional_pool_key, promotional_orders)
            
            logger.info(f"双推荐池分离完成: 用户 {user_id}, 正常池 {len(normal_orders)} 个, 推广池 {len(promotional_orders)} 个")
            
            return {
                "normal_orders": normal_orders,
                "promotional_orders": promotional_orders,
                "normal_count": len(normal_orders),
                "promotional_count": len(promotional_orders)
            }
            
        except Exception as e:
            logger.error(f"双推荐池分离失败: {str(e)}")
            # 出错时返回原始数据
            return {
                "normal_orders": orders,
                "promotional_orders": [],
                "normal_count": len(orders),
                "promotional_count": 0
            }

    def _get_promotional_orders_fallback(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        推广商单兜底机制：严格筛选版本
        
        Args:
            user_id: 用户ID
            limit: 期望返回数量
            
        Returns:
            List[Dict]: 推广商单列表
        """
        try:
            # 严格筛选推广商单
            promotional_orders = self.vector_db.get_orders_by_filters(
                {"promotion": True, "state": "WaitReceive"}, 
                limit=limit * 3  # 获取更多候选，确保筛选质量
            )
            
            if not promotional_orders:
                logger.warning(f"向量数据库中没有找到推广商单")
                return []
            
            # 二次验证：确保所有商单都是推广商单
            verified_promotional = []
            for order in promotional_orders:
                if order.get('promotion', False) and order.get('state') == 'WaitReceive':
                    verified_promotional.append(order)
            
            if not verified_promotional:
                logger.warning(f"筛选后没有有效的推广商单")
                return []
            
            # 随机选择指定数量
            import random
            random.shuffle(verified_promotional)
            selected_orders = verified_promotional[:min(limit, len(verified_promotional))]
            
            logger.info(f"兜底机制成功获取推广商单: {len(selected_orders)} 个（实际可用: {len(verified_promotional)}）")
            return selected_orders
            
        except Exception as e:
            logger.error(f"获取推广商单兜底数据失败: {str(e)}")
            return []

    def get_promotional_orders_fallback(self, user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        公开接口：获取推广商单兜底数据（当推广池取完后调用）
        
        Args:
            user_id: 用户ID
            limit: 返回数量限制
            
        Returns:
            List[Dict]: 推广商单列表
        """
        return self._get_promotional_orders_fallback(user_id, limit)


# 创建单例实例
recommendation_service = RecommendationService()

def get_recommendation_service() -> RecommendationService:
    """获取推荐服务的单例实例"""
    return recommendation_service 