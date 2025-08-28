import logging
import time
from typing import List, Dict, Any, Optional
from datetime import datetime
from services.backend_api_client import BackendAPIClient
from services.cache_service import get_cache_service
from business_milvus_db import BusinessMilvusDB

logger = logging.getLogger(__name__)

class BackendSyncService:
    """后端同步服务，处理数据同步和事件处理（精简版规则）"""
    
    def __init__(self):
        self.api_client = BackendAPIClient()
        self.cache_service = get_cache_service()
        self.vector_db = BusinessMilvusDB()
        self.last_sync_timestamp = None
        self.last_event_id = 0
        
        # 同步状态缓存键
        self.sync_status_key = "business_rec:sync:status"
    
    def get_sync_status(self) -> Dict[str, Any]:
        try:
            status = self.cache_service.get_cached_data(self.sync_status_key)
            if status:
                return status
            return {
                "last_sync_timestamp": None,
                "last_event_id": 0,
                "total_orders": 0,
                "last_sync_time": None
            }
        except Exception as e:
            logger.error(f"获取同步状态失败: {str(e)}")
            return {
                "last_sync_timestamp": None,
                "last_event_id": 0,
                "total_orders": 0,
                "last_sync_time": None
            }
    
    def set_sync_status(self, status: Dict[str, Any]) -> bool:
        try:
            self.cache_service.cache_data(self.sync_status_key, status, expire_time=86400)
            return True
        except Exception as e:
            logger.error(f"设置同步状态失败: {str(e)}")
            return False
    
    def sync_all_orders(self) -> bool:
        """全量同步：仅同步状态为WaitReceive的商单到向量库"""
        try:
            logger.info("开始全量同步商单数据...")
            if not self.api_client.health_check():
                logger.error("后端服务不可用，跳过同步")
                return False
            orders = self.api_client.get_all_orders()
            if not orders:
                logger.warning("未获取到任何商单数据")
                return False
            logger.info(f"获取到 {len(orders)} 个商单，开始筛选状态为WaitReceive并向量化...")

            # 清空现有向量数据
            self.vector_db.clear_all_orders()

            valid_orders = [o for o in orders if o.get('state') == 'WaitReceive']
            if valid_orders:
                self.vector_db.add_orders(valid_orders)
            logger.info(f"成功向量化 {len(valid_orders)} 个有效(WaitReceive)商单")

            current_timestamp = int(time.time())
            sync_status = {
                "last_sync_timestamp": current_timestamp,
                "last_event_id": 0,
                "total_orders": len(valid_orders),
                "last_sync_time": datetime.now().isoformat()
            }
            self.set_sync_status(sync_status)

            self.cache_service.clear_all_recommendations()
            logger.info("全量同步完成")
            return True
        except Exception as e:
            logger.error(f"全量同步失败: {str(e)}")
            return False
    
    def get_events_in_range(self, start_event_id: int, end_event_id: int) -> List[Dict[str, Any]]:
        """
        获取指定事件ID范围内的事件数据
        
        Args:
            start_event_id: 起始事件ID
            end_event_id: 结束事件ID
            
        Returns:
            List[Dict]: 事件列表
        """
        try:
            logger.info(f"获取事件ID范围 {start_event_id}-{end_event_id} 的事件数据")
            
            # 从后端获取所有事件
            all_events = self.api_client.get_order_events()
            if not all_events:
                logger.info("无事件数据")
                return []
            
            # 过滤指定范围内的事件
            filtered_events = []
            for event in all_events:
                event_id = event.get('id')
                if event_id:
                    try:
                        event_id_int = int(event_id) if isinstance(event_id, str) else event_id
                        if start_event_id <= event_id_int <= end_event_id:
                            filtered_events.append(event)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"事件ID类型转换失败: event_id={event_id}, 错误: {e}")
                        continue
            
            logger.info(f"事件ID范围 {start_event_id}-{end_event_id} 内找到 {len(filtered_events)} 个事件")
            return filtered_events
            
        except Exception as e:
            logger.error(f"获取事件范围数据失败: {str(e)}")
            return []
    
    def sync_events_from_backend(self) -> List[Dict[str, Any]]:
        """
        从后端同步事件数据（增量同步，避免重复）
        
        Returns:
            List[Dict]: 新同步的事件列表
        """
        try:
            logger.info("开始从后端同步事件数据...")
            
            # 获取当前同步状态
            sync_status = self.get_sync_status()
            last_event_id = sync_status.get("last_event_id", 0)
            last_sync_timestamp = sync_status.get("last_sync_timestamp")
            
            logger.info(f"上次同步状态: 事件ID={last_event_id}, 时间戳={last_sync_timestamp}")
            
            # 从后端获取事件
            all_events = self.api_client.get_order_events()
            
            if not all_events:
                logger.info("无事件数据需要同步")
                return []
            
            # 过滤新事件（基于事件ID和时间戳）
            new_events = []
            # 确保max_event_id是整数类型
            try:
                max_event_id = int(last_event_id) if isinstance(last_event_id, str) else last_event_id
            except (ValueError, TypeError):
                max_event_id = 0
            
            for event in all_events:
                event_id = event.get('id')
                event_time = event.get('operationTime')
                
                # 检查是否是新事件
                is_new = False
                
                # 方法1: 基于事件ID（确保类型一致）
                if event_id and last_event_id is not None:
                    try:
                        # 转换为整数进行比较
                        event_id_int = int(event_id) if isinstance(event_id, str) else event_id
                        last_event_id_int = int(last_event_id) if isinstance(last_event_id, str) else last_event_id
                        
                        if event_id_int > last_event_id_int:
                            is_new = True
                            max_event_id = max(max_event_id, event_id_int)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"事件ID类型转换失败: event_id={event_id}, last_event_id={last_event_id}, 错误: {e}")
                        continue
                
                # 方法2: 基于时间戳（如果ID不可用）
                elif event_time and last_sync_timestamp:
                    try:
                        event_timestamp = self._parse_event_time(event_time)
                        if event_timestamp > last_sync_timestamp:
                            is_new = True
                    except (ValueError, TypeError) as e:
                        logger.warning(f"时间戳比较失败: event_time={event_time}, last_sync_timestamp={last_sync_timestamp}, 错误: {e}")
                        continue
                
                # 方法3: 首次同步（所有事件都是新的）
                elif last_event_id == 0:
                    is_new = True
                    if event_id:
                        try:
                            event_id_int = int(event_id) if isinstance(event_id, str) else event_id
                            max_event_id = max(max_event_id, event_id_int)
                        except (ValueError, TypeError) as e:
                            logger.warning(f"事件ID类型转换失败: event_id={event_id}, 错误: {e}")
                            continue
                
                if is_new:
                    new_events.append(event)
                    logger.info(f"发现新事件: ID={event_id}, 时间={event_time}")
            
            if not new_events:
                logger.info("无新事件需要同步")
                return []
            
            logger.info(f"需要同步 {len(new_events)} 个新事件")
            
            # 处理新事件（更新向量数据库）
            processed_events = self._process_new_events(new_events)
            
            # 更新同步状态
            if processed_events:
                new_sync_status = {
                    "last_event_id": max_event_id,
                    "last_sync_timestamp": int(time.time()),
                    "total_orders": sync_status.get("total_orders", 0),
                    "last_sync_time": datetime.now().isoformat()
                }
                self.set_sync_status(new_sync_status)
                logger.info(f"同步状态已更新: 最新事件ID={max_event_id}")
            
            logger.info(f"成功同步 {len(processed_events)} 个新事件")
            return processed_events
                
        except Exception as e:
            logger.error(f"从后端同步事件失败: {str(e)}")
            return []
    
    def _process_new_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        处理新事件，基于商单状态变化更新向量数据库
        
        处理规则：
        1. 商单状态变成 WaitReceive → 插入向量数据库
        2. 商单状态从 WaitReceive 变成其他状态 → 删除向量数据库
        3. 其他状态变化 → 忽略
        
        Args:
            events: 新事件列表
            
        Returns:
            List[Dict]: 成功处理的事件列表
        """
        try:
            processed_events = []
            
            for event in events:
                try:
                    order_id = event.get('id')
                    task_number = event.get('taskNumber')
                    old_state = event.get('oldState')
                    new_state = event.get('newState')
                    
                    logger.info(f"处理事件: 商单ID={order_id}, 任务编号={task_number}, 状态变化: {old_state} -> {new_state}")
                    
                    # 规则1: 商单状态变成 WaitReceive → 插入向量数据库
                    if new_state == 'WaitReceive':
                        success = self._handle_order_insert(event)
                        if success:
                            processed_events.append(event)
                            logger.info(f"商单 {order_id} 状态变为WaitReceive，插入向量数据库成功")
                        else:
                            logger.warning(f"商单 {order_id} 插入向量数据库失败")
                    
                    # 规则2: 商单状态从 WaitReceive 变成其他状态 → 删除向量数据库
                    elif old_state == 'WaitReceive' and new_state != 'WaitReceive':
                        success = self._handle_order_delete(event)
                        if success:
                            processed_events.append(event)
                            logger.info(f"商单 {order_id} 状态从WaitReceive变为{new_state}，从向量数据库删除成功")
                        else:
                            logger.warning(f"商单 {order_id} 从向量数据库删除失败")
                    
                    # 规则3: 其他状态变化 → 忽略
                    else:
                        logger.info(f"商单 {order_id} 状态变化 {old_state} -> {new_state}，无需处理")
                        processed_events.append(event)  # 记录已处理
                        
                except Exception as e:
                    logger.error(f"处理事件失败: {str(e)}, 事件: {event}")
                    continue
            
            return processed_events
            
        except Exception as e:
            logger.error(f"批量处理事件失败: {str(e)}")
            return []
    
    def _handle_order_insert(self, event: Dict[str, Any]) -> bool:
        """处理商单插入事件（状态变为WaitReceive）"""
        try:
            # 正确提取商单ID：从extra_data中获取，而不是事件ID
            order_id = self._extract_order_id_from_event(event)
            if not order_id:
                logger.error(f"无法从事件中提取商单ID，事件ID: {event.get('id')}")
                return False
            
            logger.info(f"从事件 {event.get('id')} 中提取到商单ID: {order_id}")
            
            # 从后端获取最新的商单数据
            order_data = self.api_client.get_order_by_id(order_id)
            if not order_data:
                logger.warning(f"无法获取商单 {order_id} 的数据")
                return False
            
            # 转换为向量数据库格式
            converted_order = self.api_client._convert_order_format(order_data)
            
            # 检查商单是否已存在（避免重复插入）
            existing_order = self.vector_db.get_order_by_id(order_id)
            
            if existing_order:
                # 如果已存在，先删除再插入（确保数据是最新的）
                logger.info(f"商单 {order_id} 已存在，先删除再插入")
                self.vector_db.remove_order(str(order_id))
            
            # 插入新商单
            self.vector_db.add_orders([converted_order])
            logger.info(f"商单 {order_id} 已插入向量数据库")
            
            return True
            
        except Exception as e:
            logger.error(f"处理商单插入失败: {str(e)}")
            return False
    
    def _handle_order_delete(self, event: Dict[str, Any]) -> bool:
        """处理商单删除事件（状态从WaitReceive变为其他）"""
        try:
            # 正确提取商单ID：从extra_data中获取，而不是事件ID
            order_id = self._extract_order_id_from_event(event)
            if not order_id:
                logger.error(f"无法从事件中提取商单ID，事件ID: {event.get('id')}")
                return False
            
            logger.info(f"从事件 {event.get('id')} 中提取到商单ID: {order_id}")
            
            # 从向量数据库中删除商单
            self.vector_db.remove_order(str(order_id))
            logger.info(f"商单 {order_id} 已从向量数据库删除")
            
            return True
            
        except Exception as e:
            logger.error(f"处理商单删除失败: {str(e)}")
            return False
    
    def _extract_order_id_from_event(self, event: Dict[str, Any]) -> Optional[int]:
        """
        从事件中正确提取商单ID
        
        Args:
            event: 事件数据
            
        Returns:
            int: 商单ID，如果无法提取则返回None
        """
        try:
            # 方法1: 从data.order中获取（推荐方式）
            order_data = (event.get('data') or {}).get('order') or {}
            if order_data and isinstance(order_data, dict):
                order_id = order_data.get('id')
                if order_id and str(order_id).isdigit():
                    logger.debug(f"从data.order中提取到商单ID: {order_id}")
                    return int(order_id)
            
            # 方法2: 从extraData中解析（备用方式）
            extra_data = event.get('extraData')
            if extra_data:
                try:
                    if isinstance(extra_data, str):
                        parsed_extra = json.loads(extra_data)
                    else:
                        parsed_extra = extra_data
                    
                    if isinstance(parsed_extra, dict):
                        order_id = parsed_extra.get('id')
                        if order_id and str(order_id).isdigit():
                            logger.debug(f"从extraData中提取到商单ID: {order_id}")
                            return int(order_id)
                except (json.JSONDecodeError, TypeError) as e:
                    logger.debug(f"解析extraData失败: {str(e)}")
            
            # 方法3: 从taskNumber中尝试提取（最后兜底）
            task_number = event.get('taskNumber')
            if task_number and isinstance(task_number, str):
                # 如果taskNumber包含数字ID，尝试提取
                import re
                match = re.search(r'(\d+)', task_number)
                if match:
                    potential_id = match.group(1)
                    if len(potential_id) > 3:  # 避免提取太短的ID
                        logger.debug(f"从taskNumber中提取到潜在商单ID: {potential_id}")
                        return int(potential_id)
            
            logger.warning(f"无法从事件中提取商单ID，事件结构: {event}")
            return None
            
        except Exception as e:
            logger.error(f"提取商单ID时发生异常: {str(e)}")
            return None

    def _parse_event_time(self, time_str: str) -> int:
        """解析事件时间字符串为时间戳"""
        try:
            # 尝试多种时间格式
            import datetime
            
            # 格式1: "2024-01-01 12:00:00"
            try:
                dt = datetime.datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                return int(dt.timestamp())
            except:
                pass
            
            # 格式2: "2024-01-01T12:00:00"
            try:
                dt = datetime.datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                return int(dt.timestamp())
            except:
                pass
            
            # 格式3: 时间戳字符串
            try:
                return int(float(time_str))
            except:
                pass
            
            # 如果都失败，返回当前时间
            logger.warning(f"无法解析时间格式: {time_str}，使用当前时间")
            return int(time.time())
            
        except Exception as e:
            logger.error(f"时间解析失败: {str(e)}")
            return int(time.time())
    
    def sync_order_events(self) -> bool:
        """增量同步：仅当new_state==WaitReceive添加；只要old_state==WaitReceive且new_state!=WaitReceive强制删除（兜底）"""
        try:
            logger.info("开始增量同步商单事件...")
            sync_status = self.get_sync_status()
            last_event_id = sync_status.get("last_event_id", 0)

            latest_info = self.api_client.get_latest_event_info()
            latest_event_id = latest_info.get("latest_event_id", 0)
            event_count = latest_info.get("event_count", 0)

            if latest_event_id <= last_event_id or event_count == 0:
                logger.info("没有新事件需要同步")
                return True

            events = self.api_client.get_order_events(since_timestamp=last_event_id)
            if not events:
                logger.warning("未获取到事件数据")
                return False

            processed_count = 0
            for event in events:
                if self._process_event(event):
                    processed_count += 1

            sync_status.update({
                "last_event_id": latest_event_id,
                "last_sync_time": datetime.now().isoformat()
            })
            self.set_sync_status(sync_status)

            logger.info(f"事件同步完成: 处理 {processed_count}/{len(events)} 个事件")
            return True
        except Exception as e:
            logger.error(f"事件同步失败: {str(e)}")
            return False

    def _convert_backend_order(self, backend_order: Dict[str, Any]) -> Dict[str, Any]:
        """调用客户端相同的映射逻辑，将后端订单格式转为内部格式"""
        try:
            # 复用API客户端的映射方法
            return self.api_client._convert_order_format(backend_order)  # noqa
        except Exception as e:
            logger.error(f"订单字段转换失败: {str(e)}")
            return {}

    def _process_event(self, event: Dict[str, Any]) -> bool:
        try:
            changes = (event.get("data") or {}).get("changes") or {}
            backend_order = (event.get("data") or {}).get("order") or {}
            new_state = changes.get("new_state") or backend_order.get("state")
            old_state = changes.get("old_state")
            order_code = event.get("backend_order_code")
            event_type = event.get("event_type")

            # 兜底删除：任何从WaitReceive -> 非WaitReceive
            if old_state == "WaitReceive" and new_state != "WaitReceive":
                self._force_remove(order_code)
                return True

            # 进入WaitReceive才写入
            if new_state == "WaitReceive":
                internal_order = self._convert_backend_order(backend_order)
                if internal_order:
                    self.vector_db.add_orders([internal_order])
                    logger.info(f"事件触发入库(WaitReceive): {order_code}")
                    return True
                return False

            # 兜底删除：显式删除/接单/下架/完成等事件
            if event_type in ["order_deleted", "order_completed"]:
                self._force_remove(order_code)
                return True

            # 其他事件忽略
            return True
        except Exception as e:
            logger.error(f"处理事件失败: {str(e)}")
            return False

    def _force_remove(self, backend_order_code: str):
        """从所有推荐和向量库中强制删除指定商单（幂等）"""
        try:
            if not backend_order_code:
                return
            # 1) 清理缓存（双向映射）
            self.cache_service.remove_order_from_all_recommendations(backend_order_code)
            # 2) 删除向量库
            self.vector_db.remove_order(backend_order_code)
            logger.info(f"已强制删除商单: {backend_order_code}")
        except Exception as e:
            logger.error(f"强制删除商单失败: {backend_order_code}, {str(e)}")
    
    def get_user_orders_from_backend(self, user_id: str) -> List[Dict[str, Any]]:
        """从后端获取用户的商单数据"""
        try:
            logger.info(f"从后端获取用户 {user_id} 的商单数据")
            
            # 优化：先尝试从缓存获取
            cache_key = f"user_orders:{user_id}"
            cached_orders = self.cache_service.get_cached_data(cache_key)
            
            if cached_orders:
                logger.info(f"从缓存获取用户 {user_id} 的商单数据: {len(cached_orders)} 个")
                return cached_orders
            
            # 如果缓存没有，从后端获取
            orders = self.api_client.get_user_orders(user_id)
            if orders:
                logger.info(f"成功获取用户 {user_id} 的 {len(orders)} 个商单")
                # 缓存用户商单数据，避免重复获取
                self.cache_service.cache_data(cache_key, orders, expire_time=3600)  # 1小时过期
                return orders
            else:
                logger.info(f"用户 {user_id} 没有商单数据")
                return []
        except Exception as e:
            logger.error(f"从后端获取用户商单失败: {str(e)}")
            return [] 