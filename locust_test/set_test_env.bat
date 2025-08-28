@echo off
chcp 65001 >nul
echo ========================================
echo   设置测试环境变量
echo ========================================
echo.

REM 设置测试环境变量
set ENVIRONMENT=test
set PROCESS_NAME=locust_stress_test

echo ✅ 环境变量已设置:
echo    ENVIRONMENT=%ENVIRONMENT%
echo    PROCESS_NAME=%PROCESS_NAME%
echo.

echo 🚀 现在可以启动Locust压力测试了
echo 推荐接口和提交接口将自动跳过鉴权
echo.

echo 按任意键继续...
pause >nul
