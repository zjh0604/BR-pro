from typing import List, Dict, Any, Optional
import logging
from sqlalchemy.orm import Session
from storage.db import SessionLocal
from models.order import Order
from models.match_log import MatchLog
from models.user import User
from business_graph_db import BusinessGraphDB
from business_vector_db import BusinessVectorDB
from datetime import datetime, timedelta
import random

logger = logging.getLogger(__name__)

class ColdStartService:
    """冷启动推荐服务"""
    
    def __init__(self):
        self.graph_db = BusinessGraphDB()
        self.vector_db = BusinessVectorDB()
    
    def get_cold_start_recommendations(self, user_role: str, user_id: str, 
                                     n_results: int = 10) -> List[Dict[str, Any]]:
        """
        基于图传播算法和历史成功匹配的冷启动推荐
        
        Args:
            user_role: 用户角色
            user_id: 用户ID
            n_results: 推荐数量
            
        Returns:
            List[Dict]: 推荐商单列表
        """
        try:
            logger.info(f"开始冷启动推荐: user_role={user_role}, user_id={user_id}")
            
            # 首先获取平台商单（优先推荐）
            platform_orders = self._get_platform_orders(n_results // 3)  # 平台商单占1/3
            logger.info(f"平台商单推荐: {len(platform_orders)} 个")
            
            # 计算剩余推荐数量
            remaining_slots = n_results - len(platform_orders)
            
            all_recommendations = []
            
            # 策略1: 基于相同角色的历史成功匹配推荐（40%权重）
            same_role_recommendations = self._get_same_role_success_recommendations(user_role, user_id)
            logger.info(f"相同角色成功匹配推荐: {len(same_role_recommendations)} 个")
            all_recommendations.extend(same_role_recommendations)
            
            # 策略2: 基于图关系的上下游推荐（30%权重）
            graph_recommendations = self._get_graph_relationship_recommendations(user_role, user_id)
            logger.info(f"图关系推荐: {len(graph_recommendations)} 个")
            all_recommendations.extend(graph_recommendations)
            
            # 策略3: 基于时效性的新商单推荐（20%权重）
            fresh_recommendations = self._get_fresh_orders_recommendations(user_id)
            logger.info(f"时效性推荐: {len(fresh_recommendations)} 个")
            all_recommendations.extend(fresh_recommendations)
            
            # 策略4: 随机多样性推荐（10%权重）
            random_recommendations = self._get_random_diversity_recommendations(user_id)
            logger.info(f"随机多样性推荐: {len(random_recommendations)} 个")
            all_recommendations.extend(random_recommendations)
            
            # 去重处理
            unique_recommendations = self._deduplicate_recommendations(all_recommendations)
            
            # 按策略权重排序
            scored_recommendations = self._score_cold_start_recommendations(
                unique_recommendations, user_role
            )
            
            # 为冷启动推荐添加优先级评分
            scored_with_priority = self._score_cold_start_recommendations_with_priority(
                scored_recommendations[:remaining_slots]
            )
            
            # 合并平台商单和普通推荐
            final_recommendations = self._merge_platform_and_normal_recommendations(
                platform_orders, scored_with_priority, n_results
            )
            
            logger.info(f"冷启动推荐完成，返回 {len(final_recommendations)} 个商单")
            
            return final_recommendations
            
        except Exception as e:
            logger.error(f"冷启动推荐失败: {str(e)}")
            return self._get_fallback_recommendations(user_id, n_results)
    
    def _get_same_role_success_recommendations(self, user_role: str, exclude_user_id: str) -> List[Dict[str, Any]]:
        """
        策略1: 基于相同角色的历史成功匹配推荐
        1. 找到相同角色用户成功匹配的商单
        2. 基于这些商单找到相似的商单
        """
        try:
            db = SessionLocal()
            try:
                # 1. 获取相同角色的用户（通过他们的商单推断角色）
                # 由于User模型没有corresponding_role，我们需要从Order表获取
                same_role_user_ids = db.query(Order.user_id).filter(
                    Order.corresponding_role == user_role,
                    Order.user_id != exclude_user_id,
                    Order.is_deleted != True
                ).distinct().all()
                
                same_role_user_ids = [user_id[0] for user_id in same_role_user_ids]
                
                if not same_role_user_ids:
                    logger.info(f"未找到相同角色 {user_role} 的其他用户")
                    return []
                
                logger.info(f"找到 {len(same_role_user_ids)} 个相同角色用户")
                
                # 2. 获取这些用户成功匹配的商单
                successful_match_logs = db.query(MatchLog).filter(
                    MatchLog.user_id.in_(same_role_user_ids),
                    MatchLog.action == "accept"
                ).all()
                
                if not successful_match_logs:
                    logger.info(f"相同角色用户无成功匹配记录")
                    return []
                
                matched_order_ids = [log.order_id for log in successful_match_logs]
                logger.info(f"找到 {len(matched_order_ids)} 个成功匹配的商单")
                
                # 3. 获取这些成功匹配商单的详细信息
                successful_orders = db.query(Order).filter(
                    Order.order_id.in_(matched_order_ids)
                ).all()
                
                successful_order_data = [self._order_to_dict(order) for order in successful_orders]
                logger.info(f"成功匹配商单样本: {len(successful_order_data)} 个")
                
                # 4. 基于成功匹配的商单，使用向量数据库找相似商单
                similar_recommendations = []
                for sample_order in successful_order_data[:5]:  # 限制样本数量
                    similar_orders = self.vector_db.find_similar_orders(sample_order, n_results=10)
                    
                    # 过滤可用商单
                    available_similar = self._filter_available_orders_in_db(db, similar_orders, exclude_user_id)
                    similar_recommendations.extend(available_similar)
                
                # 为推荐结果添加策略标识和权重
                for order in similar_recommendations:
                    order["recommendation_strategy"] = "same_role_success"
                    order["strategy_weight"] = 0.4
                
                logger.info(f"基于相同角色成功匹配找到 {len(similar_recommendations)} 个相似推荐")
                return similar_recommendations
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"获取相同角色成功匹配推荐失败: {str(e)}")
            return []
    
    def _get_graph_relationship_recommendations(self, user_role: str, exclude_user_id: str) -> List[Dict[str, Any]]:
        """
        策略2: 基于图关系的上下游推荐
        根据冷启动角色的上下游关系找到关联角色发布的商单
        """
        try:
            # 1. 获取用户角色的图关系网络
            role_id = self.graph_db.get_role_id_by_name(user_role)
            if not role_id:
                logger.warning(f"未找到角色 {user_role} 的图关系")
                return []
            
            # 2. 获取1-2跳的关联角色
            related_roles = self._get_related_roles_with_depth(role_id, depth=2)
            if not related_roles:
                logger.warning(f"角色 {user_role} 无关联角色")
                return []
            
            # 3. 基于关联角色获取商单
            db = SessionLocal()
            try:
                related_role_names = [role["role_name"] for role in related_roles]
                
                # 分批查询关联角色的商单
                batch_size = 10
                all_orders = []
                
                for i in range(0, len(related_role_names), batch_size):
                    batch_roles = related_role_names[i:i + batch_size]
                    
                    orders_obj = db.query(Order).filter(
                        Order.corresponding_role.in_(batch_roles),
                        Order.user_id != exclude_user_id,
                        Order.is_deleted == False,
                        Order.status == "pending"
                    ).limit(15).all()  # 每批限制15个
                    
                    batch_orders = [self._order_to_dict(order) for order in orders_obj]
                    all_orders.extend(batch_orders)
                
                # 为推荐结果添加策略标识和权重
                for order in all_orders:
                    order["recommendation_strategy"] = "graph_relationship"
                    order["strategy_weight"] = 0.3
                    
                    # 添加关系强度信息
                    for related_role in related_roles:
                        if related_role["role_name"] == order.get("corresponding_role"):
                            order["relationship_strength"] = related_role.get("relationship_strength", 1)
                            break
                
                logger.info(f"基于图关系找到 {len(all_orders)} 个推荐")
                return all_orders
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"获取图关系推荐失败: {str(e)}")
            return []
    
    def _get_fresh_orders_recommendations(self, exclude_user_id: str) -> List[Dict[str, Any]]:
        """
        策略3: 基于时效性的新商单推荐
        选择最近创建的商单，保证时效性
        """
        try:
            db = SessionLocal()
            try:
                # 获取最近7天内创建的商单
                seven_days_ago = datetime.utcnow() - timedelta(days=7)
                
                fresh_orders_obj = db.query(Order).filter(
                    Order.user_id != exclude_user_id,
                    Order.is_deleted == False,
                    Order.status == "pending",
                    Order.created_at >= seven_days_ago
                ).order_by(Order.created_at.desc()).limit(20).all()
                
                fresh_orders = [self._order_to_dict(order) for order in fresh_orders_obj]
                
                # 为推荐结果添加策略标识和权重
                for order in fresh_orders:
                    order["recommendation_strategy"] = "fresh_orders"
                    order["strategy_weight"] = 0.2
                    
                    # 计算新鲜度评分（距离现在越近分数越高）
                    if order.get("created_at"):
                        created_time = datetime.fromisoformat(order["created_at"])
                        hours_ago = (datetime.utcnow() - created_time).total_seconds() / 3600
                        order["freshness_score"] = max(0, 1.0 - (hours_ago / (7 * 24)))  # 7天内线性衰减
                
                logger.info(f"基于时效性找到 {len(fresh_orders)} 个新商单推荐")
                return fresh_orders
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"获取时效性推荐失败: {str(e)}")
            return []
    
    def _get_random_diversity_recommendations(self, exclude_user_id: str) -> List[Dict[str, Any]]:
        """
        策略4: 随机多样性推荐
        随机选择一些商单，保证推荐的多样性，避免算法偏见
        """
        try:
            db = SessionLocal()
            try:
                # 随机获取一些不同分类的商单
                all_orders_obj = db.query(Order).filter(
                    Order.user_id != exclude_user_id,
                    Order.is_deleted == False,
                    Order.status == "pending"
                ).all()
                
                if len(all_orders_obj) <= 10:
                    random_orders = all_orders_obj
                else:
                    # 随机选择10个商单
                    random_orders_obj = random.sample(all_orders_obj, 10)
                    random_orders = [self._order_to_dict(order) for order in random_orders_obj]
                
                # 为推荐结果添加策略标识和权重
                for order in random_orders:
                    order["recommendation_strategy"] = "random_diversity"
                    order["strategy_weight"] = 0.1
                    order["diversity_score"] = random.random()  # 随机多样性评分
                
                logger.info(f"随机多样性推荐 {len(random_orders)} 个商单")
                return random_orders
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"获取随机多样性推荐失败: {str(e)}")
            return []
    
    def _filter_available_orders_in_db(self, db: Session, orders: List[Dict[str, Any]], 
                                     exclude_user_id: str) -> List[Dict[str, Any]]:
        """在数据库中过滤可用商单"""
        try:
            available_orders = []
            
            for order in orders:
                user_id = order.get('user_id')
                wish_title = order.get('wish_title')
                
                if not user_id or not wish_title or user_id == exclude_user_id:
                    continue
                
                # 检查商单是否仍然可用
                db_order = db.query(Order).filter(
                    Order.user_id == user_id,
                    Order.wish_title == wish_title,
                    Order.status == "pending",
                    Order.is_deleted == False
                ).first()
                
                if db_order:
                    order_dict = self._order_to_dict(db_order)
                    available_orders.append(order_dict)
            
            return available_orders
            
        except Exception as e:
            logger.error(f"过滤可用商单失败: {str(e)}")
            return []
    
    def _get_related_roles_with_depth(self, role_id: str, depth: int = 2) -> List[Dict[str, Any]]:
        """获取指定深度的关联角色"""
        try:
            query = f"""
            MATCH (r:Role {{id: $role_id}})-[rel*1..{depth}]-(related:Role)
            WITH related, collect(rel) as relationships
            WHERE size(relationships) > 0
            RETURN related, relationships
            LIMIT 30
            """
            
            with self.graph_db.driver.session() as session:
                result = session.run(query, {"role_id": role_id})
                related_roles = []
                
                for record in result:
                    role_info = dict(record["related"])
                    relationships = record["relationships"]
                    
                    # 计算关系强度
                    relationship_strength = len(relationships)
                    
                    related_roles.append({
                        "role_id": role_info.get("id"),
                        "role_name": role_info.get("name"),
                        "relationship_strength": relationship_strength,
                        "relationships": relationships
                    })
                
                return related_roles
                
        except Exception as e:
            logger.error(f"获取关联角色失败: {str(e)}")
            return []
    
    def _score_cold_start_recommendations(self, recommendations: List[Dict[str, Any]], 
                                        user_role: str) -> List[Dict[str, Any]]:
        """为冷启动推荐结果评分"""
        try:
            scored_recommendations = []
            
            for order in recommendations:
                # 基础评分 = 策略权重
                score = order.get("strategy_weight", 0.1)
                
                # 策略特定加分
                strategy = order.get("recommendation_strategy", "unknown")
                
                if strategy == "same_role_success":
                    # 相同角色成功匹配：额外加分
                    score += 0.3
                    
                elif strategy == "graph_relationship":
                    # 图关系推荐：基于关系强度加分
                    relationship_strength = order.get("relationship_strength", 1)
                    score += 0.2 * min(relationship_strength / 5.0, 1.0)
                    
                elif strategy == "fresh_orders":
                    # 时效性推荐：基于新鲜度加分
                    freshness_score = order.get("freshness_score", 0.5)
                    score += 0.1 * freshness_score
                    
                elif strategy == "random_diversity":
                    # 随机多样性：基础分数即可
                    diversity_score = order.get("diversity_score", 0.5)
                    score += 0.05 * diversity_score
                
                order["cold_start_score"] = score
                scored_recommendations.append(order)
            
            # 按评分排序
            scored_recommendations.sort(key=lambda x: x.get("cold_start_score", 0), reverse=True)
            
            return scored_recommendations
            
        except Exception as e:
            logger.error(f"冷启动推荐评分失败: {str(e)}")
            return recommendations
    
    def _get_fallback_recommendations(self, user_id: str, n_results: int) -> List[Dict[str, Any]]:
        """降级策略：热门推荐"""
        try:
            db = SessionLocal()
            try:
                # 简单的热门推荐：按创建时间倒序
                fallback_orders_obj = db.query(Order).filter(
                    Order.user_id != user_id,
                    Order.is_deleted == False,
                    Order.status == "pending"
                ).order_by(Order.created_at.desc()).limit(n_results).all()
                
                fallback_orders = [self._order_to_dict(order) for order in fallback_orders_obj]
                
                for order in fallback_orders:
                    order["recommendation_strategy"] = "fallback"
                    order["strategy_weight"] = 0.1
                
                return fallback_orders
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"降级推荐失败: {str(e)}")
            return []
    
    def _deduplicate_recommendations(self, recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """去重处理推荐结果"""
        try:
            seen_ids = set()
            unique_recommendations = []
            
            for order in recommendations:
                order_id = f"{order.get('user_id')}_{order.get('order_id', order.get('wish_title'))}"
                if order_id not in seen_ids:
                    seen_ids.add(order_id)
                    unique_recommendations.append(order)
            
            return unique_recommendations
            
        except Exception as e:
            logger.error(f"去重处理失败: {str(e)}")
            return recommendations
    
    def _order_to_dict(self, order: Order) -> Dict[str, Any]:
        """将Order对象转换为字典"""
        return {
            "order_id": order.order_id,
            "user_id": order.user_id,
            "corresponding_role": order.corresponding_role,
            "classification": order.classification,
            "wish_title": order.wish_title,
            "wish_details": order.wish_details,
            "status": order.status,
            "created_at": order.created_at.isoformat() if order.created_at else None,
            "is_platform_order": getattr(order, 'is_platform_order', False),
            "priority": getattr(order, 'priority', 0)
        }
    
    def _get_platform_orders(self, max_count: int = 5) -> List[Dict[str, Any]]:
        """
        获取平台商单列表，按优先级排序
        
        Args:
            max_count: 最大返回数量（如果平台商单数量少则全部返回）
            
        Returns:
            平台商单列表
        """
        try:
            db = SessionLocal()
            try:
                # 获取所有平台商单，按优先级和创建时间排序
                platform_orders = db.query(Order).filter(
                    Order.is_platform_order == True,
                    Order.is_deleted == False,
                    Order.status == "pending"
                ).order_by(
                    Order.priority.desc(),
                    Order.created_at.desc()
                ).all()
                
                # 转换为字典格式
                platform_orders_dict = [self._order_to_dict(order) for order in platform_orders]
                
                # 如果平台商单数量不多，全部返回；否则选择高优先级的
                if len(platform_orders_dict) <= max_count:
                    selected_platform_orders = platform_orders_dict
                else:
                    selected_platform_orders = platform_orders_dict[:max_count]
                
                logger.info(f"获取到 {len(platform_orders_dict)} 个平台商单，选择 {len(selected_platform_orders)} 个")
                return selected_platform_orders
                
            finally:
                db.close()
                
        except Exception as e:
            logger.error(f"获取平台商单时出错: {str(e)}")
            return []

    def _score_cold_start_recommendations_with_priority(self, recommendations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        为冷启动推荐结果添加优先级评分
        
        Args:
            recommendations: 推荐商单列表
            
        Returns:
            按优先级排序的推荐列表
        """
        try:
            # 为每个推荐添加优先级信息
            for recommendation in recommendations:
                # 从数据库获取最新的优先级信息
                db = SessionLocal()
                try:
                    order = db.query(Order).filter(
                        Order.order_id == recommendation.get('order_id')
                    ).first()
                    if order:
                        recommendation['priority'] = order.priority
                        recommendation['is_platform_order'] = order.is_platform_order
                    else:
                        recommendation['priority'] = 0
                        recommendation['is_platform_order'] = False
                finally:
                    db.close()
            
            # 检查是否有非零优先级的商单
            has_high_priority = any(order.get('priority', 0) > 0 for order in recommendations)
            
            if has_high_priority:
                # 有高优先级商单时，按优先级排序
                scored_recommendations = sorted(
                    recommendations, 
                    key=lambda x: (x.get('priority', 0), x.get('created_at', '')), 
                    reverse=True
                )
                logger.info(f"冷启动按优先级排序，最高优先级: {scored_recommendations[0].get('priority', 0) if scored_recommendations else 0}")
            else:
                # 所有商单优先级都为0时，保持原有的策略权重排序
                # 这里假设recommendations已经按策略权重排序（从_score_cold_start_recommendations得到）
                scored_recommendations = recommendations
                logger.info("冷启动所有商单优先级都为0，保持原有策略权重排序")
            
            return scored_recommendations
            
        except Exception as e:
            logger.error(f"冷启动优先级评分失败: {str(e)}")
            return recommendations

    def _merge_platform_and_normal_recommendations(self, 
                                                 platform_orders: List[Dict[str, Any]], 
                                                 normal_recommendations: List[Dict[str, Any]], 
                                                 n_results: int) -> List[Dict[str, Any]]:
        """
        合并平台商单和普通推荐，平台商单优先
        
        Args:
            platform_orders: 平台商单列表
            normal_recommendations: 普通推荐列表（已按优先级排序）
            n_results: 总推荐数量
            
        Returns:
            合并后的推荐列表
        """
        final_recommendations = []
        
        # 首先添加平台商单
        platform_count = min(len(platform_orders), n_results // 3)  # 平台商单最多占1/3
        final_recommendations.extend(platform_orders[:platform_count])
        
        # 然后添加高优先级用户商单
        remaining_slots = n_results - len(final_recommendations)
        if remaining_slots > 0 and normal_recommendations:
            # 过滤掉已经在平台商单中的订单
            platform_order_ids = {order.get('order_id') for order in platform_orders}
            filtered_normal = [order for order in normal_recommendations 
                             if order.get('order_id') not in platform_order_ids]
            final_recommendations.extend(filtered_normal[:remaining_slots])
        
        logger.info(f"合并推荐结果: {len(platform_orders)} 个平台商单 + {len(normal_recommendations)} 个普通推荐 = {len(final_recommendations)} 个最终推荐")
        return final_recommendations 