@echo off
chcp 65001 >nul
echo ========================================
echo   商业推荐系统 - Locust压力测试启动器
echo ========================================
echo.

REM 检查Python环境
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误：未找到Python环境
    echo 请确保已安装Python并添加到PATH环境变量
    pause
    exit /b 1
)

echo ✅ Python环境检查通过
echo.

REM 检查虚拟环境
if exist "myenv\Scripts\activate.bat" (
    echo 🔧 激活虚拟环境...
    call myenv\Scripts\activate.bat
    echo ✅ 虚拟环境已激活
) else (
    echo ⚠️  警告：未找到虚拟环境，使用系统Python
)

echo.

REM 安装依赖
echo 📦 安装Locust依赖...
pip install -r requirements_locust.txt
if errorlevel 1 (
    echo ❌ 依赖安装失败
    pause
    exit /b 1
)

echo ✅ 依赖安装完成
echo.

REM 启动Locust
echo 🚀 启动Locust压力测试...
echo.
echo 📋 使用说明：
echo 1. 测试将在浏览器中打开：http://localhost:8089
echo 2. 配置目标服务器地址（默认：http://localhost:8000）
echo 3. 设置并发用户数和启动测试
echo 4. 实时查看性能报告和图表
echo.

echo 按任意键启动Locust...
pause >nul

locust -f locustfile.py --host=http://localhost:8000

echo.
echo 🏁 测试结束
pause
