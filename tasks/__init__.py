# -*- coding: utf-8 -*-
"""
Tasks package - 包含所有Celery异步任务
"""

# 延迟导入任务模块，避免循环依赖
def _import_task_modules():
    """延迟导入任务模块，避免循环依赖"""
    try:
        # 先导入基础模块
        from . import recommendation_tasks
        from . import monitor_llm_tasks
        
        # 导出主要任务函数，方便其他模块导入（已移除LLM任务）
        from .recommendation_tasks import (
            cleanup_user_cache
        )
        
        return True
    except ImportError as e:
        print(f"❌ 任务模块导入失败: {e}")
        return False
    except Exception as e:
        print(f"❌ 任务模块导入异常: {e}")
        return False

# 在模块初始化时导入任务
_import_task_modules()

# 导出主要任务函数，方便其他模块导入（已移除LLM任务）
try:
    from .recommendation_tasks import (
        cleanup_user_cache
    )
    
    __all__ = [
        'recommendation_tasks',
        'monitor_llm_tasks', 
        'cleanup_user_cache'
    ]
except ImportError:
    # 如果导入失败，提供空的导出列表
    __all__ = [] 