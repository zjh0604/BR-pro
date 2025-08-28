from typing import Dict, Any, List
import logging
import time

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class FieldNormalizer:
    """
    字段标准化工具类，用于统一处理字段命名和格式
    """
    
    # 标准字段映射表（统一命名规范）
    STANDARD_FIELDS = {
        # 商单标识字段
        "id": ["id", "ID", "order_id", "orderId", "OrderId"],  # 商单ID
        "task_number": ["task_number", "taskNumber", "TaskNumber", "backend_order_code", "backendOrderCode"],  # 商单编码
        
        # 用户相关字段
        "user_id": ["user_id", "userId", "userID", "User ID", "UserID"],
        
        # 商单核心字段
        "title": ["title", "Title", "wish_title", "wishTitle", "Wish Title"],  # 商单标题
        "content": ["content", "Content", "wish_details", "wishDetails", "Wish Details"],  # 商单内容
        "industry_name": ["industry_name", "industryName", "IndustryName", "classification", "Classification"],  # 行业名称
        "full_amount": ["full_amount", "fullAmount", "FullAmount", "amount", "Amount"],  # 商单金额
        
        # 状态相关字段
        "state": ["state", "State", "status", "Status"],  # 商单状态
        "priority": ["priority", "Priority"],  # 优先级
        
        # 时间相关字段
        "create_time": ["create_time", "createTime", "CreateTime", "created_at", "createdAt"],  # 创建时间
        "update_time": ["update_time", "updateTime", "UpdateTime", "updated_at", "updatedAt"],  # 更新时间
        
        # 站点相关字段
        "site_id": ["site_id", "siteId", "SiteId", "site"],  # 站点ID
        
        # 兼容字段（保留向后兼容）
        "corresponding_role": ["corresponding_role", "correspondingRole", "Corresponding Role"],
        "is_platform_order": ["is_platform_order", "isPlatformOrder", "Is Platform Order"]
    }
    
    @classmethod
    def normalize_field_name(cls, field_name: str) -> str:
        """
        将字段名标准化为标准格式
        
        Args:
            field_name: 原始字段名
            
        Returns:
            str: 标准化后的字段名
        """
        if not field_name:
            return field_name
            
        # 转换为小写并移除多余空格
        field_name = field_name.lower().strip()
        
        # 查找匹配的标准字段
        for standard_field, variations in cls.STANDARD_FIELDS.items():
            if field_name in [v.lower() for v in variations]:
                return standard_field
                
        # 如果没有找到匹配的标准字段，返回原始字段名
        return field_name
    
    @classmethod
    def normalize_order(cls, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        标准化订单数据中的所有字段名（保持原始字段名，不进行转换）
        
        Args:
            order: 原始订单数据字典
            
        Returns:
            Dict[str, Any]: 标准化后的订单数据字典
        """
        if not order:
            return {}
            
        normalized_order = {}
        
        # 直接使用原始字段名，不进行转换
        for field_name, value in order.items():
            normalized_order[field_name] = value
        
        # 确保重要字段存在（即使原始数据中没有）
        important_fields = ["id", "userId", "taskNumber", "title", "content", "industryName", 
                           "fullAmount", "state", "createTime", "updateTime", "siteId"]
        
        for field in important_fields:
            if field not in normalized_order or normalized_order[field] is None:
                # 设置默认值
                if field == "priority":
                    normalized_order[field] = 0
                elif field == "fullAmount":
                    normalized_order[field] = 0.0  # 金额字段默认为0.0
                elif field in ["state", "industryName"]:
                    normalized_order[field] = "N/A"  # 修复字段名
                elif field == "siteId":
                    normalized_order[field] = "default"  # 修复字段名
                elif field == "content":
                    normalized_order[field] = ""  # 内容字段默认为空字符串
                elif field in ["createTime", "updateTime"]:
                    normalized_order[field] = "2024-01-01"  # 时间字段默认为有效日期
                else:
                    normalized_order[field] = ""  # 其他字段默认为空字符串
                    
        return normalized_order
    
    @classmethod
    def normalize_orders(cls, orders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        批量标准化多个订单数据
        
        Args:
            orders: 原始订单数据列表
            
        Returns:
            List[Dict[str, Any]]: 标准化后的订单数据列表
        """
        if not orders:
            return []
            
        return [cls.normalize_order(order) for order in orders]
    
    @classmethod
    def get_standard_fields(cls) -> List[str]:
        """
        获取所有标准字段名列表
        
        Returns:
            List[str]: 标准字段名列表
        """
        return list(cls.STANDARD_FIELDS.keys())
    
    @staticmethod
    def validate_order(order: Dict[str, Any]) -> Dict[str, Any]:
        """
        验证商单数据是否包含必要字段
        
        Args:
            order: 商单数据字典
            
        Returns:
            Dict: 验证结果
        """
        # 只保留真正必要的字段，放宽其他字段的限制
        required_fields = ["userId", "title"]  # 移除 industryName，只保留用户ID和标题
        
        missing_fields = []
        for field in required_fields:
            if not order.get(field):
                missing_fields.append(field)
        
        is_valid = len(missing_fields) == 0
        
        return {
            "is_valid": is_valid,
            "missing_fields": missing_fields,
            "order": order
        } 