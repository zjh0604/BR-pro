import logging
import json
import time
import hashlib
from typing import List, Dict, Any, Optional, Tuple
import numpy as np
from sentence_transformers import SentenceTransformer
import os
from pymilvus import connections, Collection, CollectionSchema, FieldSchema, DataType, utility
from services.field_normalizer import FieldNormalizer

logger = logging.getLogger(__name__)

class BusinessMilvusDB:
    """基于Milvus的向量数据库服务，支持元数据属性过滤。
    注意：当前后端不提供角色字段，对应字段在schema中保留为可选，但文本建模不依赖该字段。
    """
    
    def __init__(self, collection_name: str = "business_orders"):
        """初始化Milvus向量数据库"""
        self.collection_name = collection_name
        self.dim = 1024  # 向量维度
        
        # 连接Milvus
        self._connect_milvus()
        
        # 加载模型
        self._load_model()
        
        # 初始化Redis客户端（用于向量化缓存）
        self._init_redis_client()
        
        # 确保集合存在
        self._ensure_collection()
    
    def _connect_milvus(self):
        """连接Milvus服务"""
        try:
            milvus_host = os.getenv('MILVUS_HOST', 'localhost')
            milvus_port = os.getenv('MILVUS_PORT', '19530')
            
            connections.connect(
                alias="default",
                host=milvus_host,
                port=milvus_port
            )
            logger.info(f"成功连接到Milvus: {milvus_host}:{milvus_port}")
        except Exception as e:
            logger.error(f"连接Milvus失败: {str(e)}")
            raise
    
    def _load_model(self):
        """加载Sentence Transformer模型"""
        try:
            model_path = os.getenv('SENTENCE_TRANSFORMERS_HOME', './text2vec-large-chinese')
            if os.path.exists(model_path):
                self.model = SentenceTransformer(model_path)
                logger.info(f"加载本地模型: {model_path}")
            else:
                # 使用在线模型
                self.model = SentenceTransformer('shibing624/text2vec-large-chinese')
                logger.info("加载在线模型")
        except Exception as e:
            logger.error(f"加载模型失败: {str(e)}")
            raise
    
    def _init_redis_client(self):
        """初始化Redis客户端（用于向量化缓存）"""
        try:
            import redis
            self.redis_client = redis.Redis(
                host=os.getenv('BACKEND_REDIS_HOST', 'localhost'),
                port=int(os.getenv('BACKEND_REDIS_PORT', 6379)),
                db=int(os.getenv('BACKEND_REDIS_DB', 0)),
                password=os.getenv('BACKEND_REDIS_PASSWORD', None),
                decode_responses=True
            )
            # 测试连接
            self.redis_client.ping()
            logger.info("Redis客户端初始化成功，向量化缓存功能已启用")
        except Exception as e:
            logger.warning(f"Redis客户端初始化失败，向量化缓存功能将禁用: {e}")
            self.redis_client = None
    
    def _ensure_collection(self):
        """确保集合存在，如果不存在则创建"""
        try:
            if utility.has_collection(self.collection_name):
                self.collection = Collection(self.collection_name)
                # 加载集合到内存
                self.collection.load()
                logger.info(f"集合已存在: {self.collection_name}")
            else:
                self._create_collection()
        except Exception as e:
            logger.error(f"检查集合失败: {str(e)}")
            raise
    
    def _create_collection(self):
        """创建集合"""
        try:
            # 定义字段（直接使用后端字段名，保持一致性）
            fields = [
                FieldSchema(name="id", dtype=DataType.INT64, is_primary=True),                    # 商单ID（后端字段）
                FieldSchema(name="taskNumber", dtype=DataType.VARCHAR, max_length=100),           # 商单编码
                FieldSchema(name="userId", dtype=DataType.INT64),                                 # 发布人ID
                FieldSchema(name="industryName", dtype=DataType.VARCHAR, max_length=100),         # 行业名称
                FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=500),                # 商单标题
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=2000),             # 商单内容
                FieldSchema(name="fullAmount", dtype=DataType.FLOAT),                            # 商单金额
                FieldSchema(name="state", dtype=DataType.VARCHAR, max_length=50),                 # 商单状态
                FieldSchema(name="createTime", dtype=DataType.VARCHAR, max_length=50),            # 创建时间
                FieldSchema(name="updateTime", dtype=DataType.VARCHAR, max_length=50),            # 更新时间
                FieldSchema(name="siteId", dtype=DataType.VARCHAR, max_length=50),                # 站点ID
                FieldSchema(name="promotion", dtype=DataType.BOOL),                              # 推广广场字段，标识是否为推广商单
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.dim)          # 向量字段
            ]
            
            # 创建集合
            schema = CollectionSchema(fields, description="商单向量数据库")
            self.collection = Collection(self.collection_name, schema)
            
            # 创建索引
            index_params = {
                "metric_type": "L2",
                "index_type": "IVF_FLAT",
                "params": {"nlist": 1024}
            }
            self.collection.create_index("embedding", index_params)
            
            # 加载集合到内存
            self.collection.load()
            
            logger.info(f"成功创建集合: {self.collection_name}")
        except Exception as e:
            logger.error(f"创建集合失败: {str(e)}")
            raise
    
    def _get_embedding(self, text: str) -> List[float]:
        """获取文本的向量表示（带缓存）"""
        try:
            # 检查Redis客户端是否可用
            if not hasattr(self, 'redis_client') or self.redis_client is None:
                # Redis不可用，直接执行向量化
                embedding = self.model.encode(text)
                return embedding.tolist()
            
            # 使用统一的分区标识
            cache_key = f"business_rec:embedding:v2.0.0:{hashlib.md5(text.encode()).hexdigest()}"
            
            # 检查缓存
            cached_embedding = self.redis_client.get(cache_key)
            if cached_embedding:
                logger.debug(f"向量化缓存命中: {cache_key[:50]}...")
                return json.loads(cached_embedding)
            
            # 缓存未命中，执行向量化
            logger.debug(f"向量化缓存未命中，开始计算: {cache_key[:50]}...")
            embedding = self.model.encode(text)
            embedding_list = embedding.tolist()
            
            # 缓存结果（24小时过期）
            try:
                self.redis_client.setex(cache_key, 86400, json.dumps(embedding_list))
                logger.debug(f"向量化结果已缓存: {cache_key[:50]}...")
            except Exception as cache_error:
                logger.warning(f"缓存向量化结果失败: {cache_error}")
            
            return embedding_list
            
        except Exception as e:
            logger.error(f"获取向量失败: {str(e)}")
            raise
    
    def _prepare_order_text(self, order: Dict[str, Any]) -> str:
        """将商单信息转换为文本格式：只使用title和content作为向量"""
        normalized_order = FieldNormalizer.normalize_order(order)
        text_parts = []
        
        # 只使用title和content作为向量，其他字段作为元数据
        title = normalized_order.get('title')
        if title:
            text_parts.append(f"标题: {title}")
            
        content = normalized_order.get('content')
        if content:
            text_parts.append(f"内容: {content}")
            
        return "\n".join(text_parts)
    
    def add_orders(self, orders: List[Dict[str, Any]]):
        """添加多个商单到向量数据库"""
        try:
            # 标准化所有订单数据
            normalized_orders = FieldNormalizer.normalize_orders(orders)
            
            # 验证订单数据
            valid_orders = []
            for order in normalized_orders:
                validation = FieldNormalizer.validate_order(order)
                if validation["is_valid"]:
                    valid_orders.append(order)
                else:
                    logger.warning(f"订单数据验证失败，缺少字段: {validation['missing_fields']}")
            
            if not valid_orders:
                logger.error("没有有效的订单数据可以添加")
                return
            
            # 准备数据（使用正确的列表格式，每个字段是一个列表）
            data = [
                [],  # id
                [],  # taskNumber
                [],  # userId
                [],  # industryName
                [],  # title
                [],  # content
                [],  # fullAmount
                [],  # state
                [],  # createTime
                [],  # updateTime
                [],  # siteId
                [],  # promotion
                []   # embedding
            ]
            
            for order in valid_orders:
                text = self._prepare_order_text(order)
                embedding = self._get_embedding(text)
                
                # 将每个字段的值添加到对应的列表中
                # 智能处理ID字段：如果是数字字符串则转换，否则使用默认值
                order_id = order.get('id', 0)
                try:
                    if isinstance(order_id, str) and order_id.isdigit():
                        data[0].append(int(order_id))
                    elif isinstance(order_id, (int, float)):
                        data[0].append(int(order_id))
                    else:
                        data[0].append(0)  # 使用默认值
                except (ValueError, TypeError):
                    data[0].append(0)  # 转换失败时使用默认值
                
                data[1].append(order.get('taskNumber', ''))                # taskNumber
                
                # 智能处理userId字段
                user_id = order.get('userId', 0)
                try:
                    if isinstance(user_id, str) and user_id.isdigit():
                        data[2].append(int(user_id))
                    elif isinstance(user_id, (int, float)):
                        data[2].append(int(user_id))
                    else:
                        data[2].append(0)  # 使用默认值
                except (ValueError, TypeError):
                    data[2].append(0)  # 转换失败时使用默认值
                
                # 强制截断超长字段，确保不超过Milvus限制
                industry_name = str(order.get('industryName', ''))[:100]   # industryName max_length=100
                title = str(order.get('title', ''))[:500]                  # title max_length=500
                content = str(order.get('content', ''))[:2000]             # content max_length=2000
                
                # 额外安全检查：确保所有字段都不为None
                if industry_name is None:
                    industry_name = ""
                if title is None:
                    title = ""
                if content is None:
                    content = ""
                
                # 再次截断，确保绝对安全
                industry_name = str(industry_name)[:100]
                title = str(title)[:500]
                content = str(content)[:2000]
                
                data[3].append(industry_name)                             # industryName
                data[4].append(title)                                      # title
                data[5].append(content)                                    # content
                data[6].append(float(order.get('fullAmount', 0)))          # fullAmount
                data[7].append(order.get('state', 'pending'))              # state
                data[8].append(order.get('createTime', ''))                # createTime
                data[9].append(order.get('updateTime', ''))                # updateTime
                
                # 智能处理siteId字段（现在作为字符串）
                site_id = order.get('siteId', 'default')
                if isinstance(site_id, (int, float)):
                    data[10].append(str(site_id))  # 转换为字符串
                else:
                    data[10].append(str(site_id))  # 保持字符串格式
                
                data[11].append(order.get('promotion', False))            # promotion
                data[12].append(embedding)                                # embedding
            
            # 插入数据
            self.collection.insert(data)
            self.collection.flush()
            
            logger.info(f"成功添加 {len(valid_orders)} 个商单到Milvus")
            
        except Exception as e:
            logger.error(f"添加商单时出错: {str(e)}")
            raise
    
    def find_similar_orders_with_filters(
        self, 
        order: Dict[str, Any], 
        n_results: int = 5,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        查找相似商单，支持元数据属性过滤
        
        Args:
            order: 查询商单
            n_results: 返回结果数量
            filters: 过滤条件，支持以下字段：
                - state: 商单状态
                - industryName: 行业名称
                - siteId: 站点ID
                - amount_min/amount_max: 金额范围
                - created_at_start/created_at_end: 创建时间范围
        """
        try:
            # 准备查询文本
            text = self._prepare_order_text(order)
            query_embedding = self._get_embedding(text)
            
            # 构建查询表达式
            expr = ""
            if filters:
                conditions = []
                
                if filters.get('state'):
                    conditions.append(f'state == "{filters["state"]}"')
                
                if filters.get('industryName'):
                    conditions.append(f'industryName == "{filters["industryName"]}"')
                
                if filters.get('siteId'):
                    conditions.append(f'siteId == "{filters["siteId"]}"')
                
                if filters.get('amount_min') is not None:
                    conditions.append(f'fullAmount >= {filters["amount_min"]}')
                
                if filters.get('amount_max') is not None:
                    conditions.append(f'fullAmount <= {filters["amount_max"]}')
                
                if filters.get('created_at_start'):
                    conditions.append(f'createTime >= "{filters["created_at_start"]}"')
                
                if filters.get('created_at_end'):
                    conditions.append(f'createTime <= "{filters["created_at_end"]}"')
                
                if conditions:
                    expr = " and ".join(conditions)
            
            # 执行搜索
            search_params = {
                "metric_type": "L2",
                "params": {"nprobe": 10},
            }
            
            results = self.collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=search_params,
                limit=n_results,
                expr=expr,
                output_fields=[
                    "id", "taskNumber", "userId", "industryName", "title", "content",
                    "fullAmount", "state", "createTime", "updateTime", "siteId", "promotion"
                ]
            )
            
            # 转换结果格式
            similar_orders = []
            for hits in results:
                for hit in hits:
                    order_data = {
                        "id": hit.entity.get("id"),
                        "taskNumber": hit.entity.get("taskNumber"),
                        "userId": hit.entity.get("userId"),
                        "industryName": hit.entity.get("industryName"),
                        "title": hit.entity.get("title"),
                        "content": hit.entity.get("content"),
                        "fullAmount": hit.entity.get("fullAmount"),
                        "state": hit.entity.get("state"),
                        "createTime": hit.entity.get("createTime"),
                        "updateTime": hit.entity.get("updateTime"),
                        "siteId": hit.entity.get("siteId"),
                        "promotion": hit.entity.get("promotion", False),
                        "similarity_score": hit.score
                    }
                    similar_orders.append(order_data)
            
            logger.info(f"找到 {len(similar_orders)} 个相似商单")
            return similar_orders
            
        except Exception as e:
            logger.error(f"查找相似商单失败: {str(e)}")
            return []
    
    def cleanup_embedding_cache(self, order_id: str) -> bool:
        """清理商单的向量化缓存"""
        try:
            # 检查Redis客户端是否可用
            if not hasattr(self, 'redis_client') or self.redis_client is None:
                logger.warning("Redis客户端不可用，无法清理向量化缓存")
                return False
            
            # 获取商单数据，生成缓存键
            try:
                # 从向量数据库中获取商单信息
                order_data = self.get_orders_by_filters({"id": order_id}, limit=1)
                if order_data:
                    order = order_data[0]
                    # 生成文本并计算哈希
                    text = self._prepare_order_text(order)
                    cache_key = f"business_rec:embedding:v2.0.0:{hashlib.md5(text.encode()).hexdigest()}"
                    
                    # 删除缓存
                    result = self.redis_client.delete(cache_key)
                    if result:
                        logger.info(f"成功清理商单 {order_id} 的向量化缓存: {cache_key[:50]}...")
                    else:
                        logger.info(f"商单 {order_id} 的向量化缓存不存在或已过期")
                    
                    return True
                else:
                    logger.warning(f"商单 {order_id} 在向量数据库中不存在，无法清理缓存")
                    return False
                    
            except Exception as e:
                logger.warning(f"获取商单 {order_id} 信息失败: {e}")
                return False
                
        except Exception as e:
            logger.error(f"清理向量化缓存失败: {str(e)}")
            return False
    
    def cleanup_expired_embeddings(self):
        """清理过期的向量化缓存"""
        try:
            # 检查Redis客户端是否可用
            if not hasattr(self, 'redis_client') or self.redis_client is None:
                logger.warning("Redis客户端不可用，无法清理过期缓存")
                return False
            
            # 查找所有向量化缓存键
            pattern = "business_rec:embedding:v2.0.0:*"
            keys = self.redis_client.keys(pattern)
            
            if not keys:
                logger.info("没有找到向量化缓存键")
                return True
            
            # 检查哪些键已过期
            expired_keys = []
            for key in keys:
                if not self.redis_client.exists(key):
                    expired_keys.append(key)
            
            # 清理过期键
            if expired_keys:
                self.redis_client.delete(*expired_keys)
                logger.info(f"清理了 {len(expired_keys)} 个过期的向量化缓存")
            else:
                logger.info("没有过期的向量化缓存需要清理")
                
            return True
            
        except Exception as e:
            logger.warning(f"清理过期向量化缓存失败: {e}")
            return False
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取向量化缓存统计信息"""
        try:
            # 检查Redis客户端是否可用
            if not hasattr(self, 'redis_client') or self.redis_client is None:
                return {"error": "Redis客户端不可用"}
            
            # 查找所有向量化缓存键
            pattern = "business_rec:embedding:v2.0.0:*"
            keys = self.redis_client.keys(pattern)
            
            if not keys:
                return {"total_keys": 0, "total_size_mb": 0}
            
            # 计算总大小
            total_size = 0
            for key in keys:
                try:
                    value = self.redis_client.get(key)
                    if value:
                        total_size += len(value.encode('utf-8'))
                except Exception:
                    continue
            
            total_size_mb = total_size / (1024 * 1024)
            
            return {
                "total_keys": len(keys),
                "total_size_mb": round(total_size_mb, 2),
                "avg_size_per_key_kb": round((total_size / len(keys)) / 1024, 2) if keys else 0
            }
            
        except Exception as e:
            return {"error": f"获取缓存统计失败: {str(e)}"}
    
    def update_order(self, order_id: int, order_data: Dict[str, Any]):
        """更新商单"""
        try:
            # 删除旧数据
            self.collection.delete(f'id == {order_id}')
            
            # 添加新数据
            self.add_orders([order_data])
            
            logger.info(f"成功更新商单: {order_id}")
            
        except Exception as e:
            logger.error(f"更新商单失败: {str(e)}")
            raise
    
    def remove_order(self, order_id: str):
        """删除商单"""
        try:
            # 尝试将字符串ID转换为整数（如果可能）
            try:
                numeric_id = int(order_id)
                # 如果转换成功，使用数字ID删除
                self.collection.delete(f'id == {numeric_id}')
            except ValueError:
                # 如果无法转换为数字，尝试使用taskNumber字段删除
                self.collection.delete(f'taskNumber == "{order_id}"')
            
            self.collection.flush()
            
            logger.info(f"成功删除商单: {order_id}")
            return True
            
        except Exception as e:
            logger.error(f"删除商单失败: {str(e)}")
            return False
    
    def clear_all_orders(self):
        """清空所有商单"""
        try:
            self.collection.delete("id >= 0")
            self.collection.flush()
            
            logger.info("成功清空所有商单")
            
        except Exception as e:
            logger.error(f"清空商单失败: {str(e)}")
            raise
    
    def get_orders_by_filters(self, filters: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        """
        根据过滤条件获取商单
        
        Args:
            filters: 过滤条件
            limit: 返回数量限制
        """
        try:
            # 构建查询表达式
            conditions = []
            
            # 支持ID查询（字符串或数字）
            if filters.get('id'):
                order_id = filters.get('id')
                try:
                    # 尝试转换为数字ID
                    numeric_id = int(order_id)
                    conditions.append(f'id == {numeric_id}')
                except ValueError:
                    # 如果无法转换为数字，使用taskNumber查询
                    conditions.append(f'taskNumber == "{order_id}"')
            
            if filters.get('classification') or filters.get('industryName'):
                industry_name = filters.get('classification') or filters.get('industryName')
                conditions.append(f'industryName == "{industry_name}"')
            
            if filters.get('status') or filters.get('state'):
                state = filters.get('status') or filters.get('state')
                conditions.append(f'state == "{state}"')
            
            if filters.get('amount_min') or filters.get('fullAmount_min'):
                amount_min = filters.get('amount_min') or filters.get('fullAmount_min')
                conditions.append(f'fullAmount >= {amount_min}')
            
            if filters.get('amount_max') or filters.get('fullAmount_max'):
                amount_max = filters.get('amount_max') or filters.get('fullAmount_max')
                conditions.append(f'fullAmount <= {amount_max}')
            
            if filters.get('priority_min'):
                conditions.append(f'priority >= {filters["priority_min"]}')
            
            if filters.get('priority_max'):
                conditions.append(f'priority <= {filters["priority_max"]}')
            
            if filters.get('is_platform_order'):
                conditions.append(f'is_platform_order == {filters["is_platform_order"]}')
            
            if filters.get('user_id') or filters.get('userId'):
                user_id = filters.get('user_id') or filters.get('userId')
                conditions.append(f'userId == {user_id}')
            
            expr = " and ".join(conditions) if conditions else "id >= 0"
            
            # 执行查询
            results = self.collection.query(
                expr=expr,
                output_fields=[
                    "id", "taskNumber", "userId", "industryName", "title", "content",
                    "fullAmount", "state", "createTime", "updateTime", "siteId", "promotion"
                ],
                limit=limit
            )
            
            return results
            
        except Exception as e:
            logger.error(f"根据过滤条件获取商单失败: {str(e)}")
            return [] 

    def get_order_by_id(self, order_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取单个商单
        
        Args:
            order_id: 商单ID
            
        Returns:
            Dict: 商单数据，如果不存在返回None
        """
        try:
            # 查询指定ID的商单
            search_params = {
                "metric_type": "L2",
                "params": {"nprobe": 10}
            }
            
            # 使用ID进行精确查询
            results = self.collection.query(
                expr=f"id == {order_id}",
                output_fields=["id", "taskNumber", "userId", "industryName", "title", 
                             "content", "fullAmount", "state", "createTime", 
                             "updateTime", "siteId", "promotion"]
            )
            
            if results and len(results) > 0:
                logger.info(f"成功获取商单: ID={order_id}")
                return results[0]
            else:
                logger.info(f"商单不存在: ID={order_id}")
                return None
                
        except Exception as e:
            logger.error(f"获取商单失败: {str(e)}")
            return None 