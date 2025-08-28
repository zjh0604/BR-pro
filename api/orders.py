from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from storage.db import SessionLocal
from models.order import Order
from models.user import User
from models.match_log import MatchLog
from services.recommend_service import get_recommendation_service
from services.cache_service import get_cache_service
from datetime import datetime
import logging
import uuid
import time
from services.field_normalizer import FieldNormalizer
from business_milvus_db import BusinessMilvusDB

# 导入API监控模块
try:
    from tasks.monitor_api_responses import monitor
    MONITOR_ENABLED = True
except ImportError:
    MONITOR_ENABLED = False
    logger = logging.getLogger(__name__)
    logger.warning("API监控模块未启用")
else:
    logger = logging.getLogger(__name__)

router = APIRouter()

# 全局配置：所有模型自动转换为驼峰格式
model_config = ConfigDict(alias_generator=to_camel)

# 请求和响应模型
class OrderSubmitRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    
    # 支持多种字段命名格式（兼容后端字段）
    id: str = None  # 商单ID（后端传递）
    user_id: str = None
    userId: str = None
    user_Id: str = None

    # 商单编码
    task_number: str = None
    taskNumber: str = None
    backend_order_code: str = None
    backendOrderCode: str = None
    order_code: str = None
    orderCode: str = None

    # 标题/内容
    title: str = None
    content: str = None
    wish_title: str = None
    wishTitle: str = None
    wish_details: str = None
    wishDetails: str = None

    # 行业/金额/状态/站点
    industry_name: str = None
    industryName: str = None
    classification: str = None
    full_amount: float = None
    fullAmount: float = None
    amount: float = None
    state: str = None
    status: str = None
    site_id: str = None
    siteId: str = None

    # 时间
    create_time: str = None
    createTime: str = None
    created_at: str = None
    update_time: str = None
    updateTime: str = None
    updated_at: str = None

    # 其他
    is_platform_order: bool = False
    isPlatformOrder: bool = None
    priority: int = 0
    
    # 推广相关字段
    promotion: bool = False
    is_promotion: bool = False
    promotional: bool = False

    def __init__(self, **data):
        super().__init__(**data)
        # 统一字段名，优先使用新的标准化字段
        if self.userId and not self.user_id:
            self.user_id = self.userId
        elif self.user_Id and not self.user_id:
            self.user_id = self.user_Id
            
        # 统一商单编码字段
        if self.taskNumber and not self.task_number:
            self.task_number = self.taskNumber
        elif self.backendOrderCode and not self.task_number:
            self.task_number = self.backendOrderCode
        elif self.backend_order_code and not self.task_number:
            self.task_number = self.backend_order_code
            
        # 统一标题字段
        if self.wishTitle and not self.title:
            self.title = self.wishTitle
        elif self.wish_title and not self.title:
            self.title = self.wish_title
            
        # 统一内容字段
        if self.wishDetails and not self.content:
            self.content = self.wishDetails
        elif self.wish_details and not self.content:
            self.content = self.wish_details
            
        # 统一行业字段
        if self.industryName and not self.industry_name:
            self.industry_name = self.industryName
        elif self.classification and not self.industry_name:
            self.industry_name = self.classification
            
        # 统一金额字段
        if self.amount and not self.full_amount:
            self.full_amount = self.amount
        if self.fullAmount and not self.full_amount:
            self.full_amount = self.fullAmount
            
        # 统一状态字段
        if self.status and not self.state:
            self.state = self.status
            
        # 统一时间字段
        if self.createTime and not self.create_time:
            self.create_time = self.createTime
        elif self.created_at and not self.create_time:
            self.create_time = self.created_at
            
        if self.updateTime and not self.update_time:
            self.update_time = self.updateTime
        elif self.updated_at and not self.update_time:
            self.update_time = self.updated_at
            
        # 统一站点字段
        if self.siteId and not self.site_id:
            self.site_id = self.siteId

class OrderResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    
    order_id: int
    user_id: str
    backend_order_code: str = None  # 添加后端商单编码字段
    corresponding_role: str
    classification: str
    wish_title: str
    wish_details: str
    amount: float = None  # 金额字段
    status: str
    is_platform_order: bool
    priority: int
    created_at: str

class RecommendResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    
    user_orders: List[Dict[str, Any]]
    recommended_orders: List[Dict[str, Any]]

class AsyncRecommendResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    
    user_orders: List[Dict[str, Any]]
    recommended_orders: List[Dict[str, Any]]
    task_id: Optional[str] = None
    is_cached: bool
    recommendation_type: str

class TaskStatusResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    updated_at: Optional[int] = None

class PaginatedRecommendResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    user_orders: List[Dict[str, Any]]
    recommended_orders: List[Dict[str, Any]]
    pagination: Dict[str, Any]
    total_available: int
    has_more: bool

class RecommendRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    
    user_id: str
    page: int = 1
    page_size: int = 10
    industry_name: Optional[str] = None  # 对应后端industryName字段
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    created_at_start: Optional[str] = None  # ISO格式
    created_at_end: Optional[str] = None
    search: Optional[str] = None
    recommend_pool_id: Optional[str] = None
    site_id: Optional[str] = None  # 站点ID，用于同城匹配
    use_cache: bool = True
    refresh_strategy: str = "append"

class RecommendResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel)
    
    orders: List[Dict[str, Any]]
    total: int
    page: int
    page_size: Optional[int] = None
    is_cached: Optional[bool] = False
    recommendation_type: Optional[str] = "unknown"
    user_recommendations: Optional[List[Dict[str, Any]]] = None

class SubmitOrderResponse(BaseModel):
    status: str
    message: str
    user_id: str = Field(alias="userId")
    order_id: Optional[str] = Field(default=None, alias="orderId")
    task_number: Optional[str] = Field(default=None, alias="taskNumber")
    bidirectional_mapping: Dict[str, Any] = Field(alias="bidirectionalMapping")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/submit", response_model=SubmitOrderResponse)
def submit_order(request: OrderSubmitRequest, db: Session = Depends(get_db)):
    """
    提交商单接口 - 优化版本
    
    功能说明:
    1. 验证商单数据
    2. 直接向量化（不保存到本地数据库）
    3. 立即进行推荐计算
    4. 返回推荐结果到后端Redis
    
    Args:
        request: 商单提交请求
        
    Returns:
        包含推荐结果的响应
    """
    try:
        # 统一并组装订单数据（兼容后端字段命名），必要性校验交由 FieldNormalizer.validate_order 处理
        order_data = {
            # 标识
            "id": request.id,
            "userId": request.user_id or request.userId,
            "taskNumber": request.task_number or request.taskNumber or request.backend_order_code or request.backendOrderCode or request.order_code or request.orderCode,
            
            # 文本
            "title": request.title or request.wish_title or request.wishTitle,
            "content": request.content or request.wish_details or request.wishDetails,
            
            # 元数据
            "industryName": request.industry_name or request.industryName or request.classification,
            "fullAmount": request.full_amount or request.fullAmount or request.amount,
            "state": request.state or request.status or "pending",
            "siteId": request.site_id or request.siteId,
            "priority": request.priority or 0,
            "promotion": request.promotion or request.is_promotion or request.promotional or False,
            
            # 时间
            "createTime": request.create_time or request.createTime or request.created_at or datetime.now().isoformat(),
            "updateTime": request.update_time or request.updateTime or request.updated_at or datetime.now().isoformat(),
        }
        
        # 验证订单数据
        validation = FieldNormalizer.validate_order(order_data)
        if not validation["is_valid"]:
            raise HTTPException(
                status_code=422, 
                detail=f"订单数据验证失败，缺少字段: {validation['missing_fields']}"
            )
        
        logger.info(f"收到新商单提交: user_id={request.user_id}, backend_order_code={request.backend_order_code}")
        
        # 直接向量化，不保存到本地数据库
        recommendation_service = get_recommendation_service()
        vectorization_success = recommendation_service.process_new_order(order_data)
        
        if not vectorization_success:
            raise HTTPException(status_code=500, detail="商单向量化失败")
        
        # 立即进行推荐计算
        logger.info(f"开始为用户 {request.user_id} 计算推荐...")
        recommendation_result = recommendation_service.get_recommendations_async(
            user_id=request.user_id, 
            n_results=10
        )
        
        # 将推荐结果保存到后端Redis，并分离双推荐池
        cache_service = get_cache_service()
        if recommendation_result.get("recommended_orders"):
            # 使用双向映射结构保存推荐结果
            cache_service.set_recommendation_with_reverse_mapping(
                request.user_id, 
                recommendation_result["recommended_orders"]
            )
            logger.info(f"推荐结果已保存到后端Redis: user_id={request.user_id}")
            
            # 新增：分离双推荐池并缓存
            try:
                dual_pool_result = recommendation_service._split_recommendation_pools(
                    recommendation_result["recommended_orders"], 
                    request.user_id
                )
                logger.info(f"双推荐池分离完成: 正常池 {dual_pool_result['normal_count']} 个, 推广池 {dual_pool_result['promotional_count']} 个")
            except Exception as e:
                logger.warning(f"双推荐池分离失败: {str(e)}")
        
        # 构建响应（节约资源，只返回必要信息）
        response_dict = {
            "status": "success",
            "message": "商单提交成功，推荐结果已生成",
            "userId": request.user_id,
            "orderId": request.id,  # 使用真实的商单ID
            "taskNumber": request.task_number or request.taskNumber or request.backend_order_code,  # 商单编码
            "bidirectionalMapping": {
                "orderIdToUser": {request.id: request.user_id},
                "userToOrders": {request.user_id: [request.id]}
            }
        }
        
        # 通过SubmitOrderResponse模型进行序列化，确保所有字段都转换为驼峰格式
        response_data = SubmitOrderResponse(**response_dict)
        
        # API监控（如果启用）
        if MONITOR_ENABLED:
            try:
                monitor.record_api_call(
                    endpoint="/submit",
                    method="POST",
                    user_id=request.user_id,
                    response_time=time.time(),
                    success=True,
                    response_data=response_data.dict()
                )
            except Exception as e:
                logger.warning(f"API监控记录失败: {str(e)}")
        
        logger.info(f"商单提交完成: user_id={request.user_id}, 推荐数量={len(recommendation_result.get('recommended_orders', []))}")
        
        return response_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"提交商单时出错: {str(e)}")
        
        # API监控（如果启用）
        if MONITOR_ENABLED:
            try:
                monitor.record_api_call(
                    endpoint="/submit",
                    method="POST",
                    user_id=request.user_id if request else None,
                    response_time=time.time(),
                    success=False,
                    error_message=str(e)
                )
            except Exception as monitor_e:
                logger.warning(f"API监控记录失败: {str(monitor_e)}")
        
        raise HTTPException(status_code=500, detail=f"提交商单失败: {str(e)}")

@router.get("/recommend-async/{user_id}", response_model=AsyncRecommendResponse)
def get_recommend_async(user_id: str, n_results: int = 5, db: Session = Depends(get_db)):
    """
    异步获取用户的推荐商单（已移除LLM分析）
    1. 立即返回基于向量相似度的初步推荐
    2. 后台异步执行推荐池生成（已移除LLM分析）
    """
    try:
        # 获取推荐服务
        recommendation_service = get_recommendation_service()
        
        # 获取异步推荐结果
        result = recommendation_service.get_recommendations_async(user_id, n_results)
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取异步推荐失败: {str(e)}")

@router.get("/task-status/{user_id}/{task_id}", response_model=TaskStatusResponse)
def get_task_status(user_id: str, task_id: str):
    """
    查询异步任务状态
    
    返回值说明:
    - status: pending(等待中), processing(处理中), completed(已完成), failed(失败)
    - result: 任务完成时的结果数据
    """
    try:
        cache_service = get_cache_service()
        task_status = cache_service.get_task_status(user_id, task_id)
        
        if not task_status:
            raise HTTPException(status_code=404, detail="任务不存在或已过期")
        
        return TaskStatusResponse(
            task_id=task_id,
            status=task_status.get("status"),
            result=task_status.get("result"),
            updated_at=task_status.get("updated_at")
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询任务状态失败: {str(e)}")

@router.get("/final-recommendations/{user_id}", response_model=RecommendResponse)
def get_final_recommendations(user_id: str, n_results: int = 5):
    """
    获取推荐结果（已移除LLM分析）
    返回基于向量相似度的推荐结果
    """
    try:
        cache_service = get_cache_service()
        recommendation_service = get_recommendation_service()
        
        # 尝试获取推荐结果
        final_recommendations = cache_service.get_final_recommendations(user_id)
        if final_recommendations:
            # 获取用户订单信息
            db = SessionLocal()
            try:
                user_orders_obj = db.query(Order).filter(
                    Order.user_id == user_id,
                    Order.is_deleted == False  # 过滤已删除的商单
                ).all()
                user_orders = [{
                    "order_id": o.order_id,
                    "user_id": o.user_id,
                    "corresponding_role": o.corresponding_role,
                    "classification": o.classification,
                    "wish_title": o.wish_title,
                    "wish_details": o.wish_details,
                    "status": o.status,
                    "created_at": o.created_at.isoformat() if o.created_at else None
                } for o in user_orders_obj]
            finally:
                db.close()
            
            return RecommendResponse(
                user_orders=user_orders,
                recommended_orders=final_recommendations[:n_results]
            )
        
        # 如果没有推荐结果，检查任务状态
        task_status = cache_service.get_task_status(user_id)
        if task_status and task_status.get('status') == 'completed':
            # 任务已完成但缓存可能过期，重新执行同步推荐
            result = recommendation_service.get_recommendations(user_id, n_results)
            return result
        
        # 如果任务还在进行中或未完成，返回初步推荐
        initial_recommendations = cache_service.get_initial_recommendations(user_id)
        if initial_recommendations:
            # 获取用户订单信息
            db = SessionLocal()
            try:
                user_orders_obj = db.query(Order).filter(
                    Order.user_id == user_id,
                    Order.is_deleted == False  # 过滤已删除的商单
                ).all()
                user_orders = [{
                    "order_id": o.order_id,
                    "user_id": o.user_id,
                    "corresponding_role": o.corresponding_role,
                    "classification": o.classification,
                    "wish_title": o.wish_title,
                    "wish_details": o.wish_details,
                    "status": o.status,
                    "created_at": o.created_at.isoformat() if o.created_at else None
                } for o in user_orders_obj]
            finally:
                db.close()
            
            return RecommendResponse(
                user_orders=user_orders,
                recommended_orders=initial_recommendations[:n_results]
            )
        
        # 没有缓存，执行同步推荐
        result = recommendation_service.get_recommendations(user_id, n_results)
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取最终推荐失败: {str(e)}")

@router.delete("/cache/{user_id}")
def clear_user_cache(user_id: str):
    """清除用户的所有缓存数据"""
    try:
        cache_service = get_cache_service()
        success = cache_service.invalidate_user_cache(user_id)
        
        if success:
            return {"status": "success", "message": f"用户 {user_id} 的缓存已清除"}
        else:
            raise HTTPException(status_code=500, detail="清除缓存失败")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清除缓存时出错: {str(e)}")

# @router.post("/accept/{order_id}")
# def accept_order(order_id: int, user_id: str, db: Session = Depends(get_db)):
#     """
#     接受商单
#     
#     Args:
#         order_id: 商单ID
#         user_id: 接受方用户ID
#     
#     Note:
#         接受后的商单会自动从推荐列表中移除，防止重复推荐
#     """
#     order = db.query(Order).filter(
#         Order.order_id == order_id, 
#         Order.status == "pending",
#         Order.is_deleted == False  # 只能接受未被删除的商单
#     ).first()
#     
#     if not order:
#         raise HTTPException(status_code=404, detail="订单不存在、已被接受或已被删除")
#     
#     # 更新商单状态为已接受
#     order.status = "accepted"
#     
#     # 自动进行软删除，确保不会重复推荐
#     order.is_deleted = True
#     order.deleted_at = datetime.utcnow()
#     
#     db.commit()
#     
#     # 记录匹配日志
#     log = MatchLog(user_id=user_id, order_id=order_id, action="accept")
#     db.add(log)
#     db.delete(log)
#     db.commit()
#     
#     # 清除相关缓存，确保推荐系统不再推荐已接受的商单
#     try:
#         cache_service = get_cache_service()
#         
#         # 清除可能相关用户的缓存
#         cache_service.invalidate_user_cache(user_id)
#         cache_service.invalidate_user_cache(order.user_id)  # 清除商单发布者的缓存
#         
#         logger.info(f"商单 {order_id} 被用户 {user_id} 接受，已清除相关缓存")
#         
#     except Exception as e:
#         logger.warning(f"清除缓存失败: {str(e)}")
#     
#     return {
#         "status": "success", 
#         "message": "商单接受成功",
#         "order_id": order_id,
#         "note": "该商单已从推荐列表中移除"
#     }

# @router.post("/return/{order_id}")
# def return_order(order_id: int, user_id: str, db: Session = Depends(get_db)):
#     """
#     退还商单
#     
#     Args:
#         order_id: 商单ID
#         user_id: 退还方用户ID（必须是接受该商单的用户）
#     
#     Note:
#         退还后的商单会重新进入推荐列表备选池
#     """
#     # 查询商单
#     order = db.query(Order).filter(
#         Order.order_id == order_id,
#         Order.status == "accepted",
#         Order.is_deleted == True  # 只能退还已接受（软删除）的商单
#     ).first()
#     
#     if not order:
#         raise HTTPException(status_code=404, detail="订单不存在或未被接受")
#     
#     # 检查是否是接受该商单的用户在退还
#     # 查询最近一次接受该商单的记录
#     last_accept_log = db.query(MatchLog).filter(
#         MatchLog.order_id == order_id,
#         MatchLog.action == "accept"
#     ).order_by(MatchLog.timestamp.desc()).first()
#     
#     if not last_accept_log or last_accept_log.user_id != user_id:
#         raise HTTPException(
#             status_code=403, 
#             detail="只有接受该商单的用户才能退还"
#     )
#     
#     # 恢复商单状态
#     order.status = "pending"
#     order.is_deleted = False
#     order.deleted_at = None
    
    # db.commit()
    
    # # 记录退还日志
    # log = MatchLog(user_id=user_id, order_id=order_id, action="return")
    # db.add(log)
    # db.commit()
    
    # # 清除相关缓存，确保推荐系统能重新推荐该商单
    # try:
    #     cache_service = get_cache_service()
        
    #     # 清除相关用户的缓存
    #     cache_service.invalidate_user_cache(user_id)
    #     cache_service.invalidate_user_cache(order.user_id)  # 清除商单发布者的缓存
        
    #     # 由于商单重新进入推荐池，也需要清除其他可能相关用户的缓存
    #     # 这里可以考虑清除同角色用户的缓存
    #     logger.info(f"商单 {order_id} 被用户 {user_id} 退还，已清除相关缓存")
        
    # except Exception as e:
    #     logger.warning(f"清除缓存失败: {str(e)}")
    
    # return {
    #     "status": "success",
    #     "message": "商单退还成功",
    #     "order_id": order_id,
    #     "note": "该商单已重新进入推荐列表备选池"
    # }

# @router.get("/user/{user_id}")
# def get_user_orders(user_id: str, include_deleted: bool = False, db: Session = Depends(get_db)):
#     """
#     获取用户的所有商单
#     
#     Args:
#         user_id: 用户ID
#         include_deleted: 是否包含已删除（已接受）的商单，默认为False
#     """
#     if include_deleted:
#         # 获取所有商单，包括已删除的
#         orders = db.query(Order).filter(Order.user_id == user_id).all()
#     else:
#         # 只获取未删除的商单
#         orders = db.query(Order).filter(
#             Order.user_id == user_id,
#             Order.is_deleted == False
#         ).all()
#     
#     return {
#         "orders": [{
#             "order_id": o.order_id,
#             "user_id": o.user_id,
#             "corresponding_role": o.corresponding_role,
#             "classification": o.classification,
#             "wish_title": o.wish_title,
#             "wish_details": o.wish_details,
#             "amount": float(o.amount) if o.amount else None,  # 添加金额字段
#             "status": o.status,
#             "is_deleted": o.is_deleted,
#             "is_platform_order": o.is_platform_order,
#             "priority": o.priority,
#             "created_at": o.created_at.isoformat() if o.created_at else None,
#             "deleted_at": o.deleted_at.isoformat() if o.deleted_at else None
#         } for o in orders],
#         "total_count": len(orders),
#         "include_deleted": include_deleted
#     }

@router.get("/recommend-paginated/{user_id}", response_model=PaginatedRecommendResponse)
def get_paginated_recommendations(
    user_id: str, 
    page: int = 1, 
    page_size: int = 10,
    use_cache: bool = True,
    refresh_strategy: str = "append",  # "append" 或 "replace"
    db: Session = Depends(get_db)
):
    """
    分页获取用户的推荐商单 - 支持无限滚动
    
    Args:
        user_id: 用户ID
        page: 页码，从1开始
        page_size: 每页数量，默认10条
        use_cache: 是否使用缓存，默认True
        refresh_strategy: 刷新策略
            - "append": 追加模式，获取更多推荐内容（用于下拉刷新）
            - "replace": 替换模式，重新获取推荐内容（用于主动刷新）
    
    Returns:
        包含分页信息的推荐结果:
        - user_orders: 用户历史商单
        - recommended_orders: 当前页的推荐商单
        - pagination: 分页信息 (current_page, page_size, total_pages等)
        - total_available: 可推荐的商单总数
        - has_more: 是否还有更多数据
    """
    try:
        # 获取推荐服务
        recommendation_service = get_recommendation_service()
        
        # 调用分页推荐服务
        result = recommendation_service.recommend_orders(
            user_id=user_id,
            page=page,
            page_size=page_size,
            use_cache=use_cache,
            refresh_strategy=refresh_strategy
        )
        
        # 转换为PaginatedRecommendResponse格式
        user_orders = result.get("orders", [])[:3]  # 取前3个作为用户历史商单示例
        recommended_orders = result.get("orders", [])
        total_available = result.get("total", 0)
        current_page = result.get("page", 1)
        page_size_actual = result.get("page_size", page_size)
        
        # 计算分页信息
        total_pages = (total_available + page_size_actual - 1) // page_size_actual if page_size_actual > 0 else 1
        has_more = current_page < total_pages
        
        pagination_info = {
            "current_page": current_page,
            "page_size": page_size_actual,
            "total_pages": total_pages,
            "total_items": total_available
        }
        
        # 构造符合PaginatedRecommendResponse格式的响应
        response_data = {
            "user_orders": user_orders,
            "recommended_orders": recommended_orders,
            "pagination": pagination_info,
            "total_available": total_available,
            "has_more": has_more
        }
        
        # 记录API响应到监控器
        if MONITOR_ENABLED:
            try:
                request_params = {
                    'page': page,
                    'page_size': page_size,
                    'use_cache': use_cache,
                    'refresh_strategy': refresh_strategy
                }
                monitor.log_response(user_id, 'recommend-paginated', response_data, request_params)
            except Exception as monitor_error:
                logger.warning(f"记录API响应到监控器失败: {str(monitor_error)}")
        
        return response_data
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取分页推荐失败: {str(e)}")

# 无限滚动接口已删除
# 原因：
# 1. 功能与分页接口完全重叠
# 2. 分页接口通过连续调用 page=1,2,3... 实现相同效果
# 3. 分页接口实现更简单，状态管理更容易
# 4. 减少维护成本，统一使用分页接口
#
# 替代方案：使用 /recommend-paginated/{user_id}?page=1,2,3...
#
# 迁移指南：
# 旧接口: GET /recommend-infinite-scroll/user?offset=20&limit=10
# 新接口: GET /recommend-paginated/user?page=3&page_size=10

@router.get("/recommend-hybrid/{user_id}", response_model=AsyncRecommendResponse)
def get_hybrid_recommendations(
    user_id: str, 
    n_results: int = 10,
    preload_pool_size: int = 100,
    db: Session = Depends(get_db)
):
    """
    混合推荐接口 - 同时启动异步推荐池生成（已移除LLM分析）
    
    工作流程:
    1. 立即返回向量相似度的初步推荐
    2. 后台启动推荐池预生成任务（已移除LLM异步分析）
    3. 同时预生成大量推荐池用于后续分页
    
    Args:
        user_id: 用户ID
        n_results: 首页展示的推荐数量
        preload_pool_size: 预生成推荐池大小
    
    Returns:
        异步推荐响应，包含task_id和初步推荐
    """
    try:
        # 获取推荐服务
        recommendation_service = get_recommendation_service()
        
        # 1. 先获取异步推荐（包含初步推荐，已移除LLM任务）
        async_result = recommendation_service.get_recommendations_async(user_id, n_results)
        
        # 2. 后台预生成分页推荐池（异步执行，不阻塞响应）
        try:
            from tasks.recommendation_tasks import preload_pagination_pool
            pool_task_id = str(uuid.uuid4())
            
            # 异步预生成推荐池
            preload_task = preload_pagination_pool.apply_async(
                args=[user_id, preload_pool_size],
                task_id=pool_task_id
            )
            
            logger.info(f"启动推荐池预生成任务: user_id={user_id}, pool_size={preload_pool_size}, task_id={pool_task_id}")
            
        except Exception as e:
            logger.warning(f"启动推荐池预生成任务失败: {str(e)}")
            # 不影响主流程，继续返回异步推荐结果
        
        # 在响应中添加提示信息
        async_result["recommendation_type"] = "hybrid"
        async_result["message"] = "正在为您准备更多精彩推荐内容..."
        
        return async_result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取混合推荐失败: {str(e)}")

@router.post("/recommend/orders", response_model=RecommendResponse)
def recommend_orders(request: RecommendRequest, db: Session = Depends(get_db)):
    """
    统一的推荐接口 - 实现正确的异步流程（已移除LLM调用）
    
    执行流程：
    1. 快速返回向量相似度搜索结果
    2. 同时异步启动推荐池生成（已移除LLM精排）
    3. 支持siteId同城筛选
    4. 支持从推荐池中分页获取
    5. 自动建立反向映射用于增量更新
    """
    service = get_recommendation_service()
    
    # 使用新的推荐方法，实现正确的异步流程
    if hasattr(service, 'recommend_orders_new'):
        result = service.recommend_orders_new(
            user_id=request.user_id,
            page=request.page,
            page_size=request.page_size,
            industry_name=request.industry_name,
            amount_min=request.amount_min,
            amount_max=request.amount_max,
            created_at_start=request.created_at_start,
            created_at_end=request.created_at_end,
            search=request.search,
            recommend_pool_id=request.recommend_pool_id,
            site_id=request.site_id,
            use_cache=request.use_cache,
            refresh_strategy=request.refresh_strategy
        )
    else:
        # 降级到旧方法
        logger.warning("新的推荐方法不可用，使用旧方法")
        result = service.recommend_orders(
            user_id=request.user_id,
            page=request.page,
            page_size=request.page_size,
            industry_name=request.industry_name,
            amount_min=request.amount_min,
            amount_max=request.amount_max,
            created_at_start=request.created_at_start,
            created_at_end=request.created_at_end,
            search=request.search,
            recommend_pool_id=request.recommend_pool_id,
            site_id=request.site_id,
            use_cache=request.use_cache,
            refresh_strategy=request.refresh_strategy
        )
    
    # 建立反向映射，用于增量更新
    if result.get("orders") and len(result["orders"]) > 0:
        try:
            cache_service = get_cache_service()
            cache_service.set_recommendation_with_reverse_mapping(
                request.user_id, result["orders"]
            )
            logger.info(f"推荐结果反向映射已建立: user_id={request.user_id}, orders_count={len(result['orders'])}")
        except Exception as e:
            logger.warning(f"建立推荐反向映射失败: {str(e)}")
    
    # 新增：推荐接口分离双推荐池并存储到Redis
    try:
        if result.get("orders") and len(result["orders"]) > 0:
            # 分离双推荐池并存储到Redis
            dual_pool_result = service._split_recommendation_pools(
                result["orders"], 
                request.user_id
            )
            logger.info(f"推荐接口双推荐池分离完成: 正常池 {dual_pool_result['normal_count']} 个, 推广池 {dual_pool_result['promotional_count']} 个")
    except Exception as e:
        logger.warning(f"推荐接口双推荐池分离失败: {str(e)}")
        # 不影响主要推荐结果
    
    # 通过RecommendResponse模型进行序列化，确保所有字段都转换为驼峰格式
    # 从result中安全获取字段，提供默认值
    response_data = RecommendResponse(
        orders=result.get("orders", []),
        total=result.get("total", 0),
        page=result.get("page", 1),
        page_size=result.get("page_size", request.page_size),  # 使用请求中的page_size作为默认值
        is_cached=result.get("is_cached", False),
        recommendation_type=result.get("recommendation_type", "unknown"),
        user_recommendations=result.get("user_recommendations")
    )
    
    return response_data



@router.delete("/delete/{order_id}")
def delete_order(order_id: str, user_id: str = None, force_delete: bool = False):
    """
    删除商单接口 - 新架构版本
    
    功能说明:
    1. 通过商单ID在Redis中快速锁定失效商单在哪些用户推荐列表中
    2. 从Redis中清理该用户推荐列表中的失效商单
    3. 清理掉失效商单ID的反向映射
    4. 从Milvus向量数据库中删除对应的向量数据
    
    Args:
        order_id: 商单ID（字符串格式）
        user_id: 可选，指定用户ID进行权限校验
        force_delete: 是否强制删除，默认False
    
    Returns:
        删除结果信息
    """
    try:
        logger.info(f"开始删除商单: {order_id}")
        
        # 获取缓存服务和向量数据库服务
        cache_service = get_cache_service()
        vector_db = BusinessMilvusDB()
        
        # 1. 通过反向映射快速锁定失效商单在哪些用户推荐列表中
        logger.info(f"通过反向映射查找商单 {order_id} 影响的用户")
        affected_users = cache_service.get_order_affected_users(order_id)
        
        # 2. 验证商单是否存在于向量数据库中（如果不在推荐列表中）
        if not affected_users:
            logger.info(f"商单 {order_id} 不在任何用户推荐中，检查向量数据库")
            # 尝试在向量数据库中查找商单
            try:
                # 检查商单是否存在于向量数据库中
                existing_orders = vector_db.get_orders_by_filters({"id": order_id}, limit=1)
                if not existing_orders:
                    logger.warning(f"商单 {order_id} 在向量数据库中不存在")
                    raise HTTPException(status_code=404, detail="商单不存在")
                logger.info(f"商单 {order_id} 在向量数据库中存在，继续删除流程")
            except Exception as e:
                if "404" in str(e):
                    raise HTTPException(status_code=404, detail="商单不存在")
                logger.warning(f"检查商单存在性时出错: {str(e)}")
        else:
            logger.info(f"商单 {order_id} 影响用户: {affected_users}")
            
            # 3. 从Redis中清理该用户推荐列表中的失效商单
            logger.info(f"清理用户推荐列表中的失效商单: {order_id}")
            for affected_user_id in affected_users:
                cache_service.remove_order_from_user_recommendations(affected_user_id, order_id)
                logger.info(f"从用户 {affected_user_id} 推荐中移除商单 {order_id}")
        
        # 4. 清理掉失效商单ID的反向映射
        logger.info(f"清理失效商单 {order_id} 的反向映射")
        cache_service.clear_order_mapping(order_id)
        
        # 5. 清理商单的向量化缓存（新增）
        logger.info(f"清理商单 {order_id} 的向量化缓存")
        try:
            cleanup_result = vector_db.cleanup_embedding_cache(order_id)
            if cleanup_result:
                logger.info(f"成功清理商单 {order_id} 的向量化缓存")
            else:
                logger.warning(f"清理商单 {order_id} 的向量化缓存失败")
        except Exception as e:
            logger.warning(f"清理向量化缓存时出错: {str(e)}")
        
        # 6. 从Milvus向量数据库中删除对应的向量数据
        logger.info(f"从Milvus中删除商单向量: {order_id}")
        try:
            delete_result = vector_db.remove_order(order_id)
            if delete_result:
                logger.info(f"成功从Milvus中删除商单: {order_id}")
            else:
                logger.warning(f"从Milvus删除商单失败: {order_id}")
                # 如果向量数据库删除失败，返回错误
                raise HTTPException(status_code=500, detail="从向量数据库删除商单失败")
        except Exception as e:
            if "404" in str(e):
                raise HTTPException(status_code=404, detail="商单不存在")
            logger.error(f"从Milvus删除商单失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"删除商单失败: {str(e)}")
        
        # 6. 如果提供了user_id，清理该用户的缓存
        if user_id:
            logger.info(f"清理用户 {user_id} 的缓存")
            cache_service.invalidate_user_cache(user_id)
        
        # 7. 记录删除操作日志
        logger.info(f"商单 {order_id} 删除完成")
        
        return {
            "status": "success",
            "message": "商单删除成功",
            "order_id": order_id,
            "affected_users": len(affected_users),
            "deleted_at": datetime.now().isoformat(),
            "note": "该商单已从推荐系统中完全移除，不会再被推荐给任何用户"
        }
        
    except HTTPException:
        # 重新抛出HTTP异常
        raise
    except Exception as e:
        logger.error(f"删除商单时出错: {str(e)}")
        raise HTTPException(status_code=500, detail=f"删除商单失败: {str(e)}")

@router.put("/update-priority/{order_id}")
def update_order_priority(
    order_id: int, 
    priority: int, 
    user_id: str = None, 
    is_platform_order: bool = None,
    db: Session = Depends(get_db)
):
    """
    更新商单优先级
    
    使用场景:
    1. 用户投流提升商单优先级
   
    
    Args:
        id: 商单ID
        priority: 新的优先级 (0-10)
        user_id: 可选，指定用户ID进行权限校验
    
    Returns:
        更新结果信息
    """
    try:
        # 验证优先级范围
        if not 0 <= priority <= 10:
            raise HTTPException(status_code=400, detail="优先级必须在0-10之间")
        
        # 查询商单是否存在
        order = db.query(Order).filter(Order.order_id == order_id).first()
        if not order:
            raise HTTPException(status_code=404, detail="商单不存在")
        
        # 检查商单状态
        if order.status == "accepted":
            raise HTTPException(status_code=400, detail="已被接受的商单无法修改优先级")
        
        if order.is_deleted:
            raise HTTPException(status_code=400, detail="已删除的商单无法修改优先级")
        
        # 如果提供了user_id，检查权限（商单创建者或平台管理员）
        if user_id and order.user_id != user_id:
            # 这里可以添加平台管理员权限检查逻辑
            # 暂时只允许商单创建者修改自己的商单
            raise HTTPException(status_code=403, detail="无权限修改该商单")
        
        # 记录原始值用于日志
        old_priority = order.priority
        old_is_platform_order = order.is_platform_order
        
        # 更新优先级
        order.priority = priority
        
        # 如果提供了is_platform_order参数，更新平台商单标记
        if is_platform_order is not None:
            order.is_platform_order = is_platform_order
        
        # 更新修改时间
        order.updated_at = datetime.utcnow()
        
        db.commit()
        
        # 清除相关推荐缓存，确保优先级变更生效
        try:
            cache_service = get_cache_service()
            
            # 清除商单创建者的缓存
            cache_service.invalidate_user_cache(order.user_id)
            
            # 如果变为平台商单，清除所有用户缓存
            if is_platform_order and not old_is_platform_order:
                cache_service.invalidate_all_user_cache()
                logger.info(f"商单 {order_id} 升级为平台商单，已清除所有用户缓存")
            else:
                # 优先级变更，清除可能相关用户的缓存
                logger.info(f"商单 {order_id} 优先级变更，已清除相关缓存")
            
        except Exception as e:
            logger.warning(f"清除推荐缓存失败: {str(e)}")
            # 缓存清理失败不影响更新操作
        
        # 记录更新日志
        try:
            update_user = user_id if user_id else "system"
            action = "update_priority"
            if is_platform_order is not None:
                action = "update_priority_and_platform"
            
            log = MatchLog(
                user_id=update_user, 
                order_id=order_id, 
                action=action
            )
            db.add(log)
            db.commit()
        except Exception as e:
            logger.warning(f"记录更新日志失败: {str(e)}")
        
        # 构建响应信息
        response_data = {
            "status": "success",
            "message": "商单优先级更新成功",
            "order_id": order_id,
            "old_priority": old_priority,
            "new_priority": priority,
            "updated_at": order.updated_at.isoformat()
        }
        
        # 如果平台商单标记有变更，添加到响应中
        if is_platform_order is not None:
            response_data.update({
                "old_is_platform_order": old_is_platform_order,
                "new_is_platform_order": is_platform_order,
                "message": "商单优先级和平台商单标记更新成功"
            })
        
        return response_data
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"更新商单优先级失败: {str(e)}") 

# @router.get("/backend-order/{backend_order_code}")
# def get_order_by_backend_code(backend_order_code: str, user_id: str = None, db: Session = Depends(get_db)):
#     """
#     通过后端商单编码查询商单
#     
#     Args:
#         backend_order_code: 后端商单编码
#         user_id: 可选，指定用户ID进行权限校验
#     
#     Returns:
#         商单信息
#     """
#     try:
#         # 查询商单
#         query = db.query(Order).filter(Order.backend_order_code == backend_order_code)
#         
#         # 如果提供了user_id，添加权限校验
#         if user_id:
#             query = query.filter(Order.user_id == user_id)
#         
#         order = query.first()
#         
#         if not order:
#             raise HTTPException(status_code=404, detail="商单不存在或无权限访问")
#         
#         return {
#             "order_id": order.order_id,
#             "backend_order_code": order.backend_order_code,
#             "user_id": order.user_id,
#             "corresponding_role": order.corresponding_role,
#             "classification": order.classification,
#             "wish_title": order.wish_title,
#             "wish_details": order.wish_details,
#             "amount": order.amount,
#             "status": order.status,
#             "is_platform_order": order.is_platform_order,
#             "priority": order.priority,
#             "created_at": order.created_at.isoformat() if order.created_at else None,
#             "updated_at": order.updated_at.isoformat() if order.updated_at else None
#         }
#         
#     except HTTPException:
#         raise
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=f"查询商单失败: {str(e)}")

# @router.put("/backend-order/{backend_order_code}")
# def update_order_by_backend_code(backend_order_code: str, request: OrderSubmitRequest, db: Session = Depends(get_db)):
#     """
#     通过后端商单编码更新商单
#     
#     Args:
#         backend_order_code: 后端商单编码
#         request: 更新的商单数据
#     
#     Returns:
#         更新结果
#     """
#     try:
#         # 查询现有商单
#         order = db.query(Order).filter(
#             Order.backend_order_code == backend_order_code,
#             Order.user_id == request.user_id
#         ).first()
#         
#         if not order_id:
#             raise HTTPException(status_code=404, detail="商单不存在")
#         
#         # 检查商单状态
#         if order.status == "accepted":
#             raise HTTPException(status_code=400, detail="已被接受的商单无法修改")
#         
#         if order.is_deleted:
#             raise HTTPException(status_code=400, detail="已删除的商单无法修改")
#         
#         # 更新商单信息
#         order.corresponding_role = request.corresponding_role
#         order.corresponding_role = request.classification
#         order.wish_title = request.wish_title
#         order.wish_details = request.wish_details
#         order.amount = request.amount
#         order.is_platform_order = request.is_platform_order
#         order.priority = request.priority
#         order.updated_at = datetime.utcnow()
#         
#         db.commit()
#         
#         # 清除相关缓存
#         try:
#             cache_service = get_cache_service()
#             cache_service.invalidate_user_cache(order.user_id)
#             logger.info(f"商单 {backend_order_code} 更新后已清除用户 {order.user_id} 的推荐缓存")
#         except Exception as e:
#             logger.warning(f"清除推荐缓存失败: {str(e)}")
#         
#         return {
#             "order_id": order.order_id,
#             "backend_order_code": order.backend_order_code,
#             "status": "success",
#             "message": "商单更新成功"
#         }
#         
#     except HTTPException:
#         raise
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(status_code=500, detail=f"更新商单失败: {str(e)}")

# @router.delete("/backend-order/{backend_order_code}")
# def delete_order_by_backend_code(backend_order_code: str, user_id: str, force_delete: bool = False, db: Session = Depends(get_db)):
#     """
#     通过商单编码删除商单 (包含缓存以及向量数据库的删除)
#     
#     Args:
#         backend_order_code: 后端商单编码
#         user_id: 用户ID（权限校验）
#         #force_delete: 是否强制物理删除 (暂时不使用，默认直接强制删除)
#     
#     Returns:
#         删除结果
#     """
#     try:
#         # 查询商单
#         order = db.query(Order).filter(
#             Order.backend_order_code == backend_order_code,
#             Order.user_id == user_id
#         ).first()
#         
#         if not order:
#             raise HTTPException(status_code=404, detail="商单不存在")
#         
#         # 检查商单状态
#         if order.status == "accepted":
#             raise HTTPException(status_code=400, detail="已被接受的商单无法删除")
#         
#         if force_delete:
#             # 物理删除
#             order.is_deleted = True
#             delete_type = "物理删除"
#         else:
#             # 软删除
#             order.is_deleted = True
#             order.deleted_at = datetime.utcnow()
#             order.status = "deleted"
#             delete_type = "软删除"
#         
#         db.commit()
#         
#         # 清除相关缓存
#         try:
#             cache_service = get_cache_service()
#             cache_service.invalidate_user_cache(user_id)
#             logger.info(f"商单 {backend_order_code} 已{delete_type}，已清除用户 {user_id} 的推荐缓存")
#         except Exception as e:
#             logger.warning(f"清除推荐缓存失败: {str(e)}")
#         
#         return {
#             "backend_order_code": backend_order_code,
#             "status": "success",
#             "message": f"商单{delete_type}成功"
#         }
#         
#     except HTTPException:
#         raise
#     except Exception as e:
#         db.rollback()
#         raise HTTPException(status_code=500, detail=f"删除商单失败: {str(e)}") 

@router.get("/cache/stats")
def get_cache_stats():
    """获取缓存统计信息"""
    try:
        vector_db = BusinessMilvusDB()
        cache_stats = vector_db.get_cache_stats()
        
        return {
            "success": True,
            "data": cache_stats,
            "timestamp": int(time.time() * 1000)
        }
    except Exception as e:
        logger.error(f"获取缓存统计失败: {str(e)}")
        return {
            "success": False,
            "message": f"获取缓存统计失败: {str(e)}",
            "timestamp": int(time.time() * 1000)
        }

@router.post("/cache/cleanup")
def cleanup_cache():
    """清理过期缓存"""
    try:
        vector_db = BusinessMilvusDB()
        cleanup_result = vector_db.cleanup_expired_embeddings()
        
        if cleanup_result:
            return {
                "success": True,
                "message": "缓存清理完成",
                "timestamp": int(time.time() * 1000)
            }
        else:
            return {
                "success": False,
                "message": "缓存清理失败",
                "timestamp": int(time.time() * 1000)
            }
    except Exception as e:
        logger.error(f"清理缓存失败: {str(e)}")
        return {
            "success": False,
            "message": f"清理缓存失败: {str(e)}",
            "timestamp": int(time.time() * 1000)
        }