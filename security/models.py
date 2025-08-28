"""
安全请求数据模型
定义解密后的安全参数数据结构
"""
from pydantic import BaseModel, Field
from typing import Optional
import time

class SecureRequestPayload(BaseModel):
    """安全请求载荷数据结构"""
    
    token: Optional[str] = Field(None, description="用户登录Token，非登录接口可为空")
    userId: Optional[str] = Field(None, description="用户ID，非登录接口可为空")
    timestamp: int = Field(..., description="当前毫秒时间戳")
    url: str = Field(..., description="当前请求路径")
    platform: Optional[str] = Field(None, description="设备系统类型：Android、ios")
    nonce: str = Field(..., description="随机字符串，不低于18位")
    sign: str = Field(..., description="HMAC-SHA256签名")
    
    class Config:
        # 允许额外字段，提高兼容性
        extra = "allow"
    
    def is_expired(self, max_interval: int = 60000) -> bool:
        """
        检查请求是否过期
        
        Args:
            max_interval: 最大时间间隔（毫秒），默认1分钟
            
        Returns:
            是否过期
        """
        current_time = int(time.time() * 1000)
        return abs(current_time - self.timestamp) > max_interval
    
    def get_signature_data(self) -> str:
        """
        获取用于签名的数据字符串
        按照 a-z 排序后的参数进行拼接
        
        Returns:
            排序后的参数字符串
        """
        # 排除 sign 字段，对其他字段按字母顺序排序
        data_dict = self.dict(exclude={'sign'})
        
        # 按字母顺序排序并拼接
        sorted_items = sorted(data_dict.items(), key=lambda x: x[0])
        data_string = "&".join([f"{k}={v}" for k, v in sorted_items if v is not None])
        
        return data_string

class SecurityConfig(BaseModel):
    """安全配置"""
    
    # AES 密钥（16字节）
    aes_key: str = Field("1234567890123456", description="AES加密密钥")
    
    # HMAC 密钥
    hmac_key: str = Field("your_hmac_secret_key", description="HMAC签名密钥")
    
    # 时间戳容差（毫秒）
    timestamp_tolerance: int = Field(60000, description="时间戳容差，默认1分钟")
    
    # Nonce 过期时间（秒）
    nonce_expire_time: int = Field(120, description="Nonce过期时间，默认2分钟")
    
    # 是否启用签名验证
    enable_signature_verify: bool = Field(True, description="是否启用签名验证")
    
    # 是否启用时间戳验证
    enable_timestamp_verify: bool = Field(True, description="是否启用时间戳验证")
    
    # 是否启用 Nonce 防重放
    enable_nonce_verify: bool = Field(True, description="是否启用Nonce防重放验证")