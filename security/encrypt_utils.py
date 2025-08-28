"""
AES 加解密工具类
用于解密后端发送的加密数据，与 Java 端保持一致的加密算法
"""
import base64
import hashlib
import hmac
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
import json
import logging

logger = logging.getLogger(__name__)

class EncryptUtils:
    """AES 加解密工具类，与 Java 端保持兼容"""
    
    # 与 Java 端保持一致的密钥（16字节）
    SECRET_KEY = b'1234567890123456'  # 实际使用时应该从环境变量或配置文件读取
    
    @classmethod
    def encrypt(cls, plain_text: str) -> str:
        """
        AES 加密（用于测试）
        
        Args:
            plain_text: 待加密的明文
            
        Returns:
            加密后的 Base64 字符串
        """
        try:
            cipher = AES.new(cls.SECRET_KEY, AES.MODE_ECB)
            padded_data = pad(plain_text.encode('utf-8'), AES.block_size)
            encrypted_data = cipher.encrypt(padded_data)
            return base64.b64encode(encrypted_data).decode('utf-8')
        except Exception as e:
            logger.error(f"AES 加密失败: {e}")
            raise
    
    @classmethod
    def decrypt(cls, cipher_text: str) -> str:
        """
        AES 解密
        
        Args:
            cipher_text: Base64 编码的密文
            
        Returns:
            解密后的明文
        """
        try:
            cipher = AES.new(cls.SECRET_KEY, AES.MODE_ECB)
            encrypted_data = base64.b64decode(cipher_text)
            decrypted_data = cipher.decrypt(encrypted_data)
            return unpad(decrypted_data, AES.block_size).decode('utf-8')
        except Exception as e:
            logger.error(f"AES 解密失败: {e}")
            raise
    
    @staticmethod
    def generate_hmac_signature(data: str, key: str) -> str:
        """
        生成 HMAC-SHA256 签名
        
        Args:
            data: 待签名的数据
            key: 签名密钥
            
        Returns:
            Base64 编码的签名
        """
        try:
            signature = hmac.new(
                key.encode('utf-8'),
                data.encode('utf-8'),
                hashlib.sha256
            ).digest()
            return base64.b64encode(signature).decode('utf-8')
        except Exception as e:
            logger.error(f"HMAC 签名生成失败: {e}")
            raise
    
    @staticmethod
    def verify_hmac_signature(data: str, key: str, signature: str) -> bool:
        """
        验证 HMAC-SHA256 签名
        
        Args:
            data: 原始数据
            key: 签名密钥
            signature: 待验证的签名
            
        Returns:
            验证结果
        """
        try:
            expected_signature = EncryptUtils.generate_hmac_signature(data, key)
            return hmac.compare_digest(expected_signature, signature)
        except Exception as e:
            logger.error(f"HMAC 签名验证失败: {e}")
            return False