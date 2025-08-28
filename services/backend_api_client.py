import requests
import logging
import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import json

logger = logging.getLogger(__name__)

class BackendAPIClient:
    """后端API客户端，用于数据同步"""
    
    def __init__(self):
        # 环境配置
        self.environment = os.getenv('BACKEND_ENVIRONMENT', 'test')
        
        # 根据环境设置基础URL
        if self.environment == 'test':
            self.base_url = 'http://192.168.150.240:31080'
        elif self.environment == 'pre':
            self.base_url = 'https://api-pre.sohuglobal.com'
        elif self.environment == 'prod':
            self.base_url = 'https://api.sohuglobal.com'
        else:
            self.base_url = os.getenv('BACKEND_API_URL', 'http://192.168.150.240:31080')
        
        self.timeout = int(os.getenv('BACKEND_API_TIMEOUT', 30))
        self.session = requests.Session()
        
        # 设置请求头
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'BusinessRec-Sync/2.0.0'
        })
    
    def get_all_orders(self, since_timestamp: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        获取所有有效商单
        
        Args:
            since_timestamp: 可选的时间戳，只获取此时间之后的商单
            
        Returns:
            List[Dict]: 商单列表
        """
        try:
            # 根据后端回复，使用分页逻辑获取所有商单
            # id=0 获取第一页，然后使用最后一条记录的ID继续查询
            logger.info("🔍 使用分页逻辑获取所有商单...")
            
            all_orders = []
            current_id = 0  # 从0开始，获取第一页
            
            while True:
                try:
                    response = self.session.get(
                        f"{self.base_url}/open/busy/task/list",
                        params={'id': current_id},
                        timeout=self.timeout
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        if result.get('code') == 200:  # 注意：返回码是200
                            orders_data = result.get('data', [])
                            if orders_data:
                                # 转换字段格式
                                converted_orders = [self._convert_order_format(order) for order in orders_data]
                                all_orders.extend(converted_orders)
                                logger.info(f"✅ 第{len(all_orders)//100 + 1}页: 获取到 {len(converted_orders)} 个商单")
                                
                                # 获取最后一条记录的ID，作为下次查询条件
                                last_order = orders_data[-1]
                                next_id = last_order.get('id')
                                
                                if next_id and next_id > current_id:
                                    current_id = next_id
                                    logger.info(f"🔄 下一页查询ID: {current_id}")
                                else:
                                    logger.info("🏁 已到达最后一页")
                                    break
                            else:
                                logger.info("📝 当前页无数据，查询完成")
                                break
                        else:
                            logger.warning(f"⚠️ 接口返回错误: {result.get('msg')}")
                            break
                    else:
                        logger.warning(f"⚠️ HTTP状态码异常: {response.status_code}")
                        break
                        
                except Exception as e:
                    logger.error(f"❌ 查询异常: {str(e)}")
                    break
                
                # 防止无限循环
                if len(all_orders) > 10000:  # 最多获取10000条记录
                    logger.warning("⚠️ 达到最大记录数限制，停止查询")
                    break
            
            logger.info(f"🎯 总共获取到 {len(all_orders)} 个商单")
            return all_orders
                
        except Exception as e:
            logger.error(f"❌ 获取商单异常: {str(e)}")
            return []
    
    def get_user_orders(self, user_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
        """
        获取用户历史商单
        
        Args:
            user_id: 用户ID
            include_deleted: 是否包含已删除的商单
            
        Returns:
            List[Dict]: 用户商单列表
        """
        try:
            # 调用查询商单接口，获取所有商单后过滤用户
            all_orders = self.get_all_orders()
            user_orders = [order for order in all_orders if order.get('user_id') == user_id]
            
            if not include_deleted:
                # 过滤掉已删除的商单
                user_orders = [order for order in user_orders if order.get('status') not in ['Delete', 'OffShelf']]
            
            return user_orders
                
        except Exception as e:
            logger.error(f"从后端获取用户商单失败: {str(e)}")
            return []
    
    def get_order_events(self, since_timestamp: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        获取商单事件（使用轮询方式，支持ID跳跃）
        
        Args:
            since_timestamp: 可选的时间戳，只获取此时间之后的事件
            limit: 返回事件数量限制
            
        Returns:
            List[Dict]: 事件列表
        """
        try:
            logger.info("🔍 使用轮询方式获取事件数据...")
            
            all_events = []
            current_event_id = 1  # 从事件ID 1开始轮询
            max_attempts = 1000   # 最大尝试次数，防止无限循环
            consecutive_failures = 0  # 连续失败次数
            max_consecutive_failures = 50  # 最大连续失败次数，允许更多跳跃
            
            while len(all_events) < limit and current_event_id <= max_attempts:
                try:
                    # 轮询获取事件
                    response = self.session.get(
                        f"{self.base_url}/open/busy/task/operation/log",
                        params={'id': current_event_id},
                        timeout=self.timeout
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        if result.get('code') == 200:  # 成功返回码是200
                            event_data = result.get('data')
                            if event_data:
                                # 处理返回的数据（可能是列表或单个对象）
                                if isinstance(event_data, list):
                                    # 如果是列表，处理每个元素
                                    new_events_count = 0
                                    for item in event_data:
                                        # 检查是否已经存在相同的事件ID，避免重复
                                        event_id = item.get('id')
                                        if event_id and not any(e.get('id') == event_id for e in all_events):
                                            # 检查是否有有效的商单数据
                                            extra_data = item.get('extraData')
                                            if extra_data and extra_data != '(Null)':
                                                event = self._convert_operation_log_to_event(item)
                                                if event:  # 确保转换成功
                                                    all_events.append(event)
                                                    new_events_count += 1
                                                    consecutive_failures = 0  # 重置连续失败计数
                                                    logger.debug(f"✅ 成功获取新事件 ID={event_id}")
                                                else:
                                                    logger.debug(f"⚠️ 事件 ID={event_id} 转换失败")
                                                    consecutive_failures += 1
                                            else:
                                                logger.debug(f"⚠️ 事件 ID={event_id} 商单数据为空，跳过")
                                                consecutive_failures += 1
                                        else:
                                            logger.debug(f"⚠️ 事件 ID={event_id} 已存在，跳过")
                                    
                                    if new_events_count > 0:
                                        logger.debug(f"✅ 本次查询获取到 {new_events_count} 个新事件")
                                    else:
                                        logger.debug(f"📝 本次查询无新事件")
                                        consecutive_failures += 1
                                else:
                                    # 如果是单个对象
                                    event = self._convert_operation_log_to_event(event_data)
                                    if event:  # 确保转换成功
                                        all_events.append(event)
                                        consecutive_failures = 0  # 重置连续失败计数
                                        logger.debug(f"✅ 成功获取事件 ID={current_event_id}")
                                    else:
                                        logger.debug(f"⚠️ 事件 ID={current_event_id} 转换失败")
                                        consecutive_failures += 1
                            else:
                                logger.debug(f"📝 事件 ID={current_event_id} 无数据")
                                consecutive_failures += 1
                        else:
                            logger.debug(f"⚠️ 事件 ID={current_event_id} 返回错误: {result.get('msg')}")
                            consecutive_failures += 1
                    else:
                        logger.debug(f"⚠️ 事件 ID={current_event_id} HTTP状态码异常: {response.status_code}")
                        consecutive_failures += 1
                        
                except Exception as e:
                    logger.debug(f"⚠️ 获取事件 ID={current_event_id} 异常: {str(e)}")
                    consecutive_failures += 1
                
                current_event_id += 1
                
                # 如果连续失败次数过多，可能已经到达末尾
                if consecutive_failures >= max_consecutive_failures:
                    logger.info(f"📝 连续 {consecutive_failures} 次失败，可能已到达事件末尾")
                    break
                
                # 如果已经获取到足够的事件，提前退出
                if len(all_events) >= limit:
                    logger.info(f"✅ 已获取到 {len(all_events)} 个事件，达到限制")
                    break
            
            # 按时间排序
            all_events.sort(key=lambda x: x.get('operationTime', ''))
            
            # 应用时间过滤
            if since_timestamp:
                all_events = [event for event in all_events 
                            if self._parse_time(event.get('operationTime', '')) >= since_timestamp]
            
            # 应用数量限制
            all_events = all_events[-limit:] if len(all_events) > limit else all_events
            
            logger.info(f"✅ 轮询方式获取到 {len(all_events)} 个事件")
            return all_events
                
        except Exception as e:
            logger.error(f"获取事件异常: {str(e)}")
            return []
    
    def _get_order_operation_log(self, order_id: int) -> List[Dict[str, Any]]:
        """
        获取单个商单的操作日志
        
        Args:
            order_id: 商单ID
            
        Returns:
            List[Dict]: 操作日志列表
        """
        try:
            response = self.session.get(
                f"{self.base_url}/open/busy/task/operation/log",
                params={'id': order_id},
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 200:  # 修正返回码判断
                    logs = result.get('data', [])
                    # 转换为事件格式
                    events = [self._convert_operation_log_to_event(log) for log in logs]
                    return events
                else:
                    logger.warning(f"获取商单 {order_id} 操作日志失败: {result.get('msg')}")
                    return []
            else:
                logger.warning(f"获取商单 {order_id} 操作日志失败: {response.status_code}")
                return []
                
        except Exception as e:
            logger.error(f"获取商单 {order_id} 操作日志异常: {str(e)}")
            return []
    
    def get_latest_event_info(self) -> Dict[str, Any]:
        """
        获取最新事件信息
        
        Returns:
            Dict: 包含最新事件ID和事件数量
        """
        try:
            # 获取所有事件
            all_events = self.get_order_events()
            
            if not all_events:
                return {"latest_event_id": 0, "event_count": 0}
            
            # 获取最新事件时间
            latest_time = max(event.get('operation_time', '') for event in all_events)
            event_count = len(all_events)
            
            return {
                "latest_event_id": self._parse_time(latest_time),
                "event_count": event_count,
                "latest_event_time": latest_time
            }
                
        except Exception as e:
            logger.error(f"获取最新事件信息异常: {str(e)}")
            return {"latest_event_id": 0, "event_count": 0}
    
    def search_orders(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        搜索商单
        
        Args:
            filters: 筛选条件
            
        Returns:
            List[Dict]: 筛选后的商单列表
        """
        try:
            # 获取所有商单
            all_orders = self.get_all_orders()
            
            # 应用筛选条件
            filtered_orders = []
            for order in all_orders:
                if self._apply_search_filters(order, filters):
                    filtered_orders.append(order)
            
            return filtered_orders
                
        except Exception as e:
            logger.error(f"搜索商单异常: {str(e)}")
            return []
    
    def get_order_by_code(self, backend_order_code: str) -> Optional[Dict[str, Any]]:
        """
        根据商单编码获取商单详情
        
        Args:
            backend_order_code: 后端商单编码
            
        Returns:
            Dict: 商单详情，如果不存在则返回None
        """
        try:
            # 获取所有商单后查找
            all_orders = self.get_all_orders()
            for order in all_orders:
                if order.get('backend_order_code') == backend_order_code:
                    return order
            return None
                
        except Exception as e:
            logger.error(f"获取商单详情异常: {str(e)}")
            return None
    
    def get_order_by_id(self, order_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取单个商单数据
        
        Args:
            order_id: 商单ID
            
        Returns:
            Dict: 商单数据，如果不存在返回None
        """
        try:
            logger.info(f"获取商单数据: ID={order_id}")
            
            # 尝试多种方式获取商单数据
            order_data = None
            
            # 方法1: 直接通过ID查询
            order_data = self._get_order_direct(order_id)
            if order_data:
                return order_data
            
            # 方法2: 通过分页查询查找（处理后端分页逻辑）
            order_data = self._get_order_by_pagination(order_id)
            if order_data:
                return order_data
            
            # 方法3: 尝试获取所有商单后查找（兜底方案）
            logger.info(f"尝试通过全量查询获取商单 {order_id}")
            all_orders = self.get_all_orders()
            if all_orders:
                for order in all_orders:
                    if order.get('id') == order_id:
                        logger.info(f"通过全量查询找到商单: ID={order_id}")
                        return order
            
            logger.warning(f"商单ID {order_id} 在所有查询方式中均未找到")
            return None
            
        except Exception as e:
            logger.error(f"获取商单异常: {str(e)}")
            return None
    
    def _get_order_direct(self, order_id: int) -> Optional[Dict[str, Any]]:
        """直接通过ID查询商单"""
        try:
            response = self.session.get(
                f"{self.base_url}/open/busy/task/list",
                params={'id': order_id},
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get('code') == 200:
                    orders_data = result.get('data', [])
                    if orders_data:
                        # 找到匹配的商单
                        for order in orders_data:
                            if order.get('id') == order_id:
                                logger.info(f"直接查询成功获取商单: ID={order_id}")
                                return order
                        
                        logger.warning(f"直接查询：商单ID {order_id} 在返回数据中未找到")
                    else:
                        logger.warning(f"直接查询：商单ID {order_id} 返回空数据")
                else:
                    logger.warning(f"直接查询失败: {result.get('msg')}")
            else:
                logger.warning(f"直接查询HTTP状态码异常: {response.status_code}")
            
            return None
            
        except Exception as e:
            logger.error(f"直接查询商单异常: {str(e)}")
            return None
    
    def _get_order_by_pagination(self, order_id: int) -> Optional[Dict[str, Any]]:
        """通过分页查询查找商单"""
        try:
            logger.info(f"通过分页查询查找商单: ID={order_id}")
            
            # 尝试不同的起始ID进行分页查询
            start_ids = [0, order_id - 100, order_id - 50, order_id - 10]
            
            for start_id in start_ids:
                if start_id < 0:
                    continue
                    
                try:
                    response = self.session.get(
                        f"{self.base_url}/open/busy/task/list",
                        params={'id': start_id},
                        timeout=self.timeout
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        if result.get('code') == 200:
                            orders_data = result.get('data', [])
                            if orders_data:
                                # 在返回数据中查找目标商单
                                for order in orders_data:
                                    if order.get('id') == order_id:
                                        logger.info(f"分页查询成功获取商单: ID={order_id}, 起始ID={start_id}")
                                        return order
                                
                                logger.debug(f"分页查询起始ID={start_id}未找到商单{order_id}")
                            else:
                                logger.debug(f"分页查询起始ID={start_id}返回空数据")
                        else:
                            logger.debug(f"分页查询起始ID={start_id}失败: {result.get('msg')}")
                    else:
                        logger.debug(f"分页查询起始ID={start_id}HTTP状态码异常: {response.status_code}")
                        
                except Exception as e:
                    logger.debug(f"分页查询起始ID={start_id}异常: {str(e)}")
                    continue
            
            logger.warning(f"分页查询：商单ID {order_id} 在所有分页中均未找到")
            return None
            
        except Exception as e:
            logger.error(f"分页查询商单异常: {str(e)}")
            return None
    
    def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            bool: 后端服务是否可用
        """
        try:
            # 健康检查应该测试接口是否真正可用，而不仅仅是HTTP连接
            response = self.session.get(
                f"{self.base_url}/open/busy/task/list",
                params={'id': 1},  # 使用有效的ID进行测试
                timeout=5
            )
            
            if response.status_code == 200:
                result = response.json()
                # 检查接口是否真正可用（返回正确的数据结构）
                # 注意：根据测试，返回码是200，不是0
                if result.get('code') is not None and 'data' in result:
                    logger.info("✅ 后端接口健康检查通过")
                    return True
                else:
                    logger.warning(f"⚠️  后端接口返回格式异常: {result}")
                    return False
            else:
                logger.error(f"❌ 后端接口HTTP状态码异常: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ 后端健康检查失败: {str(e)}")
            return False
    
    def _convert_order_format(self, backend_order: Dict[str, Any]) -> Dict[str, Any]:
        """
        转换后端商单格式为内部格式
        
        Args:
            backend_order: 后端商单数据
            
        Returns:
            Dict: 转换后的商单数据
        """
        try:
            # 解析extraData（如果存在）
            extra_data = {}
            if backend_order.get('extraData'):
                try:
                    extra_data = json.loads(backend_order['extraData']) if isinstance(backend_order['extraData'], str) else backend_order['extraData']
                except:
                    extra_data = {}
            
            # 合并数据，extraData优先级更高
            order_data = {**backend_order, **extra_data}
            
            # 字段映射（已更新为后端字段）
            converted = {
                "id": order_data.get('id'),
                "taskNumber": order_data.get('taskNumber'),
                "userId": str(order_data.get('userId')),
                "industryName": order_data.get('industryName'),
                "title": order_data.get('title'),
                "content": order_data.get('content'),
                "fullAmount": float(order_data.get('fullAmount', 0)),
                "state": order_data.get('state'),
                "createTime": order_data.get('createTime'),
                "updateTime": order_data.get('updateTime'),
                "siteId": order_data.get('siteId'),
                "priority": 0,  # 默认值，需要后端补充
                "promotion": order_data.get('promotion', False)  # 推广广场字段，默认为False
            }
            
            return converted
            
        except Exception as e:
            logger.error(f"转换商单格式失败: {str(e)}")
            return backend_order
    
    def _convert_operation_log_to_event(self, operation_log: Dict[str, Any]) -> Dict[str, Any]:
        """
        将操作日志转换为事件格式
        
        Args:
            operation_log: 操作日志
            
        Returns:
            Dict: 事件数据
        """
        try:
            # 解析extraData（存储商单的json对象）
            extra_data = {}
            if operation_log.get('extraData'):
                try:
                    extra_data = json.loads(operation_log['extraData']) if isinstance(operation_log['extraData'], str) else operation_log['extraData']
                except:
                    extra_data = {}
            
            # 确定事件类型
            operation_type = operation_log.get('operationType', '')
            event_type = self._map_operation_type_to_event_type(operation_type)
            
            # 构建事件数据 - 保持与接口文档一致的字段名
            event = {
                "id": operation_log.get('id'),
                "taskNumber": operation_log.get('taskNumber'),
                "operationType": operation_type,
                "operationTime": operation_log.get('operationTime'),
                "extraData": operation_log.get('extraData', ''),
                "userId": operation_log.get('userId'),
                "receiverId": operation_log.get('receiverId', 0),
                "title": operation_log.get('title'),
                "oldState": operation_log.get('oldState'),
                "newState": operation_log.get('newState'),
                "operatorId": operation_log.get('operatorId'),
                "remark": operation_log.get('remark', ''),
                # 同时保留兼容的字段名
                "event_id": f"{operation_log.get('id')}_{operation_log.get('operationTime')}",
                "event_type": event_type,
                "backend_order_code": operation_log.get('taskNumber'),
                "timestamp": operation_log.get('operationTime'),
                "data": {
                    "order": extra_data,  # 完整商单数据
                    "changes": {
                        "old_state": operation_log.get('oldState'),
                        "new_state": operation_log.get('newState'),
                        "operation_type": operation_type,
                        "operator_id": operation_log.get('operatorId'),
                        "remark": operation_log.get('remark', '')
                    }
                }
            }
            
            return event
            
        except Exception as e:
            logger.error(f"转换操作日志失败: {str(e)}")
            return {}
    
    def _map_operation_type_to_event_type(self, operation_type: str) -> str:
        """
        映射操作类型到事件类型
        
        Args:
            operation_type: 后端操作类型
            
        Returns:
            str: 事件类型
        """
        mapping = {
            'Create': 'order_created',
            'UpdateState': 'order_updated',
            'Finish': 'order_completed',
            'Delete': 'order_deleted',
            'OffShelf': 'order_deleted',
            'OnShelf': 'order_created'
        }
        
        return mapping.get(operation_type, 'order_updated')
    
    def _apply_search_filters(self, order: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        """
        应用搜索筛选条件
        
        Args:
            order: 商单数据
            filters: 筛选条件
            
        Returns:
            bool: 是否通过筛选
        """
        try:
            # 分类筛选
            if filters.get('classification') and order.get('classification') != filters['classification']:
                return False
            
            # 状态筛选
            if filters.get('status') and order.get('status') != filters['status']:
                return False
            
            # 金额范围筛选
            amount = order.get('amount', 0)
            if filters.get('amount_min') is not None and amount < filters['amount_min']:
                return False
            if filters.get('amount_max') is not None and amount > filters['amount_max']:
                return False
            
            # 用户ID筛选
            if filters.get('user_id') and order.get('user_id') != filters['user_id']:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"应用搜索筛选失败: {str(e)}")
            return True
    
    def _parse_time(self, time_str: str) -> int:
        """
        解析时间字符串为时间戳
        
        Args:
            time_str: 时间字符串
            
        Returns:
            int: 时间戳
        """
        try:
            if not time_str:
                return 0
            
            # 解析格式：2024-04-26 19:28:58
            dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
            return int(dt.timestamp())
            
        except Exception as e:
            logger.error(f"解析时间失败: {str(e)}")
            return 0 