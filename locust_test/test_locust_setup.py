#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Locust环境配置验证脚本
快速检查Locust是否正常工作
"""

import sys
import subprocess
import requests
import time

def check_python_version():
    """检查Python版本"""
    print("🔍 检查Python版本...")
    version = sys.version_info
    print(f"   Python版本: {version.major}.{version.minor}.{version.micro}")
    
    if version.major < 3 or (version.major == 3 and version.minor < 7):
        print("   ❌ 需要Python 3.7或更高版本")
        return False
    
    print("   ✅ Python版本符合要求")
    return True

def check_dependencies():
    """检查依赖包"""
    print("\n🔍 检查依赖包...")
    
    try:
        import locust
        print(f"   ✅ Locust已安装: {locust.__version__}")
    except ImportError:
        print("   ❌ Locust未安装")
        return False
    
    try:
        import requests
        print(f"   ✅ Requests已安装: {requests.__version__}")
    except ImportError:
        print("   ❌ Requests未安装")
        return False
    
    return True

def check_server_connectivity():
    """检查服务器连接性"""
    print("\n🔍 检查服务器连接性...")
    
    test_urls = [
        "http://localhost:8000",
        "http://localhost:8000/docs",
        "http://localhost:8000/recommend"
    ]
    
    for url in test_urls:
        try:
            response = requests.get(url, timeout=5)
            print(f"   ✅ {url}: HTTP {response.status_code}")
        except requests.exceptions.ConnectionError:
            print(f"   ❌ {url}: 连接失败")
        except requests.exceptions.Timeout:
            print(f"   ⚠️  {url}: 连接超时")
        except Exception as e:
            print(f"   ❌ {url}: 错误 - {str(e)}")
    
    return True

def test_locust_command():
    """测试Locust命令"""
    print("\n🔍 测试Locust命令...")
    
    try:
        result = subprocess.run(
            ["locust", "--version"], 
            capture_output=True, 
            text=True, 
            timeout=10
        )
        if result.returncode == 0:
            print(f"   ✅ Locust命令可用: {result.stdout.strip()}")
            return True
        else:
            print(f"   ❌ Locust命令失败: {result.stderr}")
            return False
    except FileNotFoundError:
        print("   ❌ 未找到locust命令")
        return False
    except subprocess.TimeoutExpired:
        print("   ❌ Locust命令执行超时")
        return False

def generate_test_config():
    """生成测试配置建议"""
    print("\n📋 测试配置建议:")
    print("=" * 50)
    print("1. 基础压力测试:")
    print("   - 并发用户数: 10-50")
    print("   - 启动速率: 1用户/秒")
    print("   - 测试时长: 5-10分钟")
    print()
    print("2. 中等压力测试:")
    print("   - 并发用户数: 50-200")
    print("   - 启动速率: 5用户/秒")
    print("   - 测试时长: 10-20分钟")
    print()
    print("3. 高压力测试:")
    print("   - 并发用户数: 200-1000")
    print("   - 启动速率: 10用户/秒")
    print("   - 测试时长: 20-30分钟")
    print()
    print("4. 极限压力测试:")
    print("   - 并发用户数: 1000+")
    print("   - 启动速率: 20用户/秒")
    print("   - 测试时长: 30分钟+")
    print("=" * 50)

def main():
    """主函数"""
    print("🚀 Locust环境配置验证")
    print("=" * 50)
    
    # 检查各项配置
    checks = [
        check_python_version(),
        check_dependencies(),
        check_server_connectivity(),
        test_locust_command()
    ]
    
    print("\n" + "=" * 50)
    print("📊 检查结果汇总:")
    
    if all(checks):
        print("🎉 所有检查通过！Locust环境配置正确")
        print("✅ 可以开始压力测试")
        generate_test_config()
        
        print("\n🚀 启动测试:")
        print("1. 双击运行: run_locust_test.bat")
        print("2. 或命令行运行: locust -f locustfile.py")
        print("3. 访问Web界面: http://localhost:8089")
        
    else:
        print("❌ 部分检查未通过，请解决以下问题:")
        if not checks[0]:
            print("   - 升级Python版本到3.7+")
        if not checks[1]:
            print("   - 运行: pip install -r requirements_locust.txt")
        if not checks[2]:
            print("   - 确保FastAPI服务器运行在localhost:8000")
        if not checks[3]:
            print("   - 检查Locust安装和PATH配置")
    
    print("\n按Enter键退出...")
    input()

if __name__ == "__main__":
    main()
