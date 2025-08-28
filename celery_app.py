from celery import Celery
import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# Redis配置
REDIS_HOST = os.getenv('REDIS_HOST', '127.0.0.1')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_DB = int(os.getenv('REDIS_DB', 0))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD', None)

# 构建Redis URL
if REDIS_PASSWORD:
    redis_url = f'redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'
else:
    redis_url = f'redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}'

# 创建Celery应用
app = Celery(
    'business_rec',
    broker=redis_url,
    backend=redis_url,
    include=['tasks.recommendation_tasks', 'tasks.monitor_llm_tasks']
)

# 确保任务模块被导入（延迟导入避免循环依赖）
def _import_task_modules():
    """延迟导入任务模块，避免循环依赖"""
    try:
        # 先导入基础模块
        import tasks
        print("✅ 基础任务模块导入成功")
        
        # 延迟导入具体任务模块
        from tasks import recommendation_tasks, monitor_llm_tasks
        print("✅ 具体任务模块导入成功")
        
        return True
    except ImportError as e:
        print(f"❌ 任务模块导入失败: {e}")
        return False
    except Exception as e:
        print(f"❌ 任务模块导入异常: {e}")
        return False

# 在应用启动时导入任务模块
_import_task_modules()

# Celery配置
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Asia/Shanghai',
    enable_utc=True,
    result_expires=3600,  # 结果过期时间：1小时
    
    # 优化超时控制
    task_soft_time_limit=180,  # 任务软超时：3分钟（优化后）
    task_time_limit=240,       # 任务硬超时：4分钟（优化后）
    
    # 优化并发性能
    worker_prefetch_multiplier=2,  # 提高到2，增加并发处理能力（优化后）
    worker_max_tasks_per_child=50, # 降低到50，更频繁重启worker防止内存泄漏（优化后）
    
    # 任务路由优化 - 暂时禁用特殊路由，统一使用default队列
    # task_routes={
    #     'tasks.recommendation_tasks.analyze_recommendations_with_llm': {
    #         'queue': 'llm_analysis',
    #         'routing_key': 'llm_analysis'
    #     },
    #     'tasks.monitor_llm_tasks.*': {
    #         'queue': 'monitoring',
    #         'routing_key': 'monitoring'
    #     }
    # },
    
    # 任务重试配置
    task_acks_late=True,           # 任务完成后再确认
    worker_disable_rate_limits=False,  # 启用速率限制
    
    # 队列配置
    task_default_queue='default',
    task_default_exchange='default',
    task_default_exchange_type='direct',
    task_default_routing_key='default',
    
    # 日志配置
    worker_log_color=False,  # 在生产环境禁用彩色日志
)

if __name__ == '__main__':
    app.start() 