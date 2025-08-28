import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from api import orders
from storage.db import Base, engine
from security import SecurityMiddleware, SecurityConfig
import logging
import asyncio
from contextlib import asynccontextmanager

# 加载.env文件
load_dotenv()

# 强制设置测试环境
os.environ['TESTING'] = 'true'

# 从环境变量读取密钥
AES_KEY = os.getenv("AES_KEY")
HMAC_KEY = os.getenv("HMAC_KEY")

if not AES_KEY or not HMAC_KEY:
    raise RuntimeError("AES_KEY 和 HMAC_KEY 必须通过环境变量配置！")

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("🚀 商单推荐系统启动中...")
    
    try:
        # 1. 初始化向量数据库
        logger.info("📥 初始化向量数据库...")
        from services.vector_db_initializer import get_vector_db_initializer
        
        initializer = get_vector_db_initializer()
        if initializer.health_check():
            # 异步初始化向量数据库
            asyncio.create_task(_init_vector_db_async(initializer))
        else:
            logger.warning("⚠️ 向量数据库初始化服务不可用")
        
        # 2. 启动事件同步任务
        logger.info("🔄 启动事件同步任务...")
        asyncio.create_task(_start_event_sync())
        
        logger.info("✅ 商单推荐系统启动完成")
        
    except Exception as e:
        logger.error(f"❌ 启动过程中出现错误: {str(e)}")
    
    yield
    
    # 关闭时执行
    logger.info("🛑 商单推荐系统关闭中...")

async def _init_vector_db_async(initializer):
    """异步初始化向量数据库"""
    try:
        result = initializer.initialize_vector_database(max_orders=1000)
        if result.get('success'):
            logger.info(f"✅ 向量数据库初始化成功: {result.get('inserted_count')} 个商单")
        else:
            logger.error(f"❌ 向量数据库初始化失败: {result.get('error')}")
    except Exception as e:
        logger.error(f"❌ 向量数据库初始化异常: {str(e)}")

async def _start_event_sync():
    """启动事件同步任务"""
    try:
        from services.backend_sync_service import BackendSyncService
        
        sync_service = BackendSyncService()
        
        # 每5分钟同步一次事件
        while True:
            try:
                logger.info("🔄 开始同步事件数据...")
                events = sync_service.sync_events_from_backend()
                if events:
                    logger.info(f"✅ 同步到 {len(events)} 个事件")
                    # 这里可以添加事件处理逻辑
                else:
                    logger.info("📝 无新事件需要同步")
                    
            except Exception as e:
                logger.error(f"❌ 事件同步失败: {str(e)}")
            
            # 等待5分钟(5分钟同步一次)
            await asyncio.sleep(300)
            
    except Exception as e:
        logger.error(f"❌ 启动事件同步任务失败: {str(e)}")

# 创建FastAPI应用
app = FastAPI(
    title="商单推荐系统API",
    description="基于向量搜索和LLM的智能商单推荐系统",
    version="1.0.0",
    lifespan=lifespan
)

# 安全配置
security_config = SecurityConfig(
    aes_key=AES_KEY,  # 从环境变量读取
    hmac_key=HMAC_KEY,  # 从环境变量读取
    timestamp_tolerance=60000,  # 时间戳容差：1分钟
    nonce_expire_time=120,  # Nonce 过期时间：2分钟
    enable_signature_verify=True,  # 启用签名验证
    enable_timestamp_verify=True,  # 启用时间戳验证
    enable_nonce_verify=True  # 启用 Nonce 防重放
)

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# 注册路由
app.include_router(orders.router, prefix="/api/orders", tags=["商单管理"])

# 直接注册推荐接口，避免路径前缀问题
from api.orders import router as orders_router
app.include_router(orders_router, prefix="", tags=["推荐接口"])

@app.get("/")
async def root():
    return {
        "message": "商单推荐系统API", 
        "docs": "/docs",
        "health": "ok",
        "security": "enabled"
    }

@app.get("/health")
async def health_check():
    """健康检查接口（白名单）"""
    return {
        "status": "healthy",
        "timestamp": "2024-01-01T00:00:00Z"
    }



@app.get("/favicon.ico")
async def favicon():
    """处理favicon.ico请求"""
    return FileResponse("static/favicon.ico")

# 添加安全中间件（必须在路由注册之后添加）
app.add_middleware(SecurityMiddleware, config=security_config)

if __name__ == "__main__":
    import uvicorn
    logger.info("启动商单推荐系统API服务...")
    logger.info("安全中间件已启用，接口鉴权功能已激活")
    uvicorn.run(app, host="0.0.0.0", port=8000)