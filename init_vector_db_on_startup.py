#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
服务启动时向量数据库初始化脚本
用于在服务启动时自动从后端API获取商单数据并初始化向量数据库
"""

import os
import sys
import logging
import time

# 设置环境变量
os.environ['BACKEND_ENVIRONMENT'] = 'test'

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def init_vector_db_on_startup():
    """服务启动时初始化向量数据库"""
    try:
        logger.info("🚀 服务启动时向量数据库初始化开始...")
        
        # 导入初始化服务
        from services.vector_db_initializer import get_vector_db_initializer
        
        # 获取初始化器实例
        initializer = get_vector_db_initializer()
        
        # 健康检查
        if not initializer.health_check():
            logger.error("❌ 向量数据库初始化服务健康检查失败")
            return False
        
        logger.info("✅ 向量数据库初始化服务健康检查通过")
        
        # 执行初始化
        logger.info("📥 开始从后端API获取商单数据并初始化向量数据库...")
        init_result = initializer.initialize_vector_database(max_orders=1000)
        
        if init_result.get('success'):
            logger.info("🎉 向量数据库初始化成功!")
            logger.info(f"   总商单数: {init_result.get('orders_count', 0)}")
            logger.info(f"   成功插入: {init_result.get('inserted_count', 0)}")
            logger.info(f"   插入失败: {init_result.get('failed_count', 0)}")
            logger.info(f"   处理时间: {init_result.get('processing_time', 0):.2f}秒")
            logger.info(f"   成功率: {init_result.get('success_rate', 0):.1f}%")
            return True
        else:
            logger.error("❌ 向量数据库初始化失败")
            logger.error(f"   错误信息: {init_result.get('error', '未知错误')}")
            return False
            
    except Exception as e:
        logger.error(f"❌ 向量数据库初始化异常: {str(e)}")
        return False

def main():
    """主函数"""
    print("=" * 80)
    print("🚀 服务启动时向量数据库初始化")
    print("=" * 80)
    
    start_time = time.time()
    
    # 执行初始化
    success = init_vector_db_on_startup()
    
    processing_time = time.time() - start_time
    
    print("\n" + "=" * 80)
    print("📊 初始化结果")
    print("=" * 80)
    
    if success:
        print("✅ 向量数据库初始化成功")
    else:
        print("❌ 向量数据库初始化失败")
    
    print(f"⏱️  总耗时: {processing_time:.2f}秒")
    print("=" * 80)
    
    return success

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⏹️  初始化被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 初始化过程中出现未预期的错误: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1) 