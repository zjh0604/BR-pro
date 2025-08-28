@echo off
echo ================================
echo   商单推荐系统 - Windows启动脚本 (重构版)
echo ================================

echo.
echo 1. 检查Redis服务状态...
sc query "Redis" | findstr "RUNNING" >nul
if %errorlevel%==0 (
    echo ✅ Redis服务已运行 (本地开发环境)
    echo 📝 注意：生产环境将使用后端Redis 10-16分区
) else (
    echo ❌ Redis服务未运行，请手动启动
    echo    方法：服务管理器中启动Redis服务
    echo    或者使用后端Redis服务
    pause
    exit /b 1
)

echo.
echo 2. 检查Milvus向量数据库状态...
docker-compose ps | findstr "milvus" >nul
if %errorlevel%==0 (
    echo ✅ Milvus服务已运行
) else (
    echo ❌ Milvus服务未运行，正在启动...
    docker-compose up -d etcd minio milvus
    if %errorlevel%==0 (
        echo ✅ Milvus服务启动成功
    ) else (
        echo ❌ Milvus服务启动失败
        pause
        exit /b 1
    )
)

echo.
echo 3. 激活Python虚拟环境...
cd /d "C:\Users\admin\Desktop\Business-Rec-2"
call myenv\Scripts\activate
if %errorlevel%==0 (
    echo ✅ 虚拟环境激活成功
) else (
    echo ❌ 虚拟环境激活失败
    pause
    exit /b 1
)

echo.
echo 4. 检查依赖包...
python -c "import pymilvus, sentence_transformers, qianfan" 2>nul
if %errorlevel%==0 (
    echo ✅ 核心依赖包检查通过
) else (
    echo ❌ 依赖包缺失，请先安装：pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo 5. 启动Celery Worker (后台)...
start /B celery -A celery_app worker -l info --pool=solo

echo.
echo 6. 等待Celery启动...
timeout /t 3 /nobreak >nul

echo.
echo 7. 启动FastAPI服务...
echo 📝 API服务将在前台运行，按Ctrl+C可停止所有服务
echo 🌐 访问地址: http://localhost:8000
echo 📊 API文档: http://localhost:8000/docs
echo 🔍 健康检查: http://localhost:8000/health
echo.
echo 📋 服务说明：
echo    - 使用Milvus向量数据库 (替代chromadb)
echo    - 调用后端接口获取商单数据
echo    - 事件驱动更新向量数据库
echo    - 简化推荐逻辑 (移除角色信息)
echo.
python main.py

echo.
echo 服务已停止
pause 