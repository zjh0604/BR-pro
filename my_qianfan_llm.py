# 已移除LLM功能 - 此文件仅保留占位符
# 所有LLM相关代码已被注释或移除，以支持无LLM的推荐系统

import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 已移除LLM功能
logger.info("LLM功能已移除，系统将使用纯向量相似度推荐")

# 占位符类，保持接口兼容性
class RetryableQianfanLLM:
    """已移除LLM功能的占位符类"""
    
    def __init__(self):
        logger.warning("LLM功能已被移除，此类仅用于保持接口兼容性")
    
    def invoke(self, prompt: str, max_retries: int = 3, retry_delay: int = 5) -> str:
        """已移除LLM功能，返回默认响应"""
        logger.warning("LLM调用已被移除，返回默认响应")
        return "LLM功能已被移除，系统使用向量相似度推荐"

# 创建占位符实例
llm = RetryableQianfanLLM()

# 已移除的测试函数
def test_qianfan(max_retries=3, retry_delay=5):
    """已移除LLM测试功能"""
    logger.warning("LLM测试功能已被移除")
    return False

# 已移除的rank_indices方法（保持接口兼容性）
def rank_indices(prompt: str, num_return: int = 5) -> list:
    """已移除LLM排序功能，返回默认排序"""
    logger.warning("LLM排序功能已被移除，返回默认排序")
    return list(range(1, min(num_return + 1, 6)))

# 为保持兼容性，将rank_indices方法添加到llm实例
llm.rank_indices = rank_indices

if __name__ == "__main__":
    logger.info("LLM功能已被移除，系统使用纯向量相似度推荐")


