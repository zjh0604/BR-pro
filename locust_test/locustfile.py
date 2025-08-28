#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Locust压力测试脚本 - 商业推荐系统
测试推荐接口和提交接口的性能
"""

import time
import random
import json
from locust import HttpUser, task, between, events
from typing import Dict, Any


class BusinessRecommendationUser(HttpUser):
    """
    商业推荐系统用户模拟类
    模拟真实用户访问推荐接口和提交商单的行为
    """
    
    # 用户等待时间：1-3秒
    wait_time = between(1, 3)
    
    def on_start(self):
        """用户启动时的初始化"""
        self.test_user_id = f"test_user_{random.randint(1000, 9999)}"
        self.test_site_id = f"site_{random.randint(1, 5)}"
        
        # 测试数据
        self.test_order_data = {
            "user_id": self.test_user_id,
            "taskNumber": f"TEST_{int(time.time())}_{random.randint(100, 999)}",
            "title": f"测试商单标题_{random.randint(1, 100)}",
            "content": f"这是一个测试商单内容，用于压力测试。随机ID: {random.randint(1000, 9999)}",
            "industryName": random.choice(["软件开发", "设计服务", "营销推广", "技术咨询", "数据分析"]),
            "fullAmount": round(random.uniform(100, 10000), 2),
            "state": "WaitReceive",
            "siteId": self.test_site_id,
            "priority": random.randint(0, 5),
            "promotion": random.choice([True, False])
        }
        
        print(f"✅ 用户 {self.test_user_id} 初始化完成，站点: {self.test_site_id}")
    
    @task(3)  # 权重3：70%的概率访问推荐接口
    def test_recommend_interface(self):
        """测试推荐接口性能"""
        try:
            # 构建请求参数
            params = {
                "page": random.randint(1, 3),
                "page_size": random.choice([5, 10, 20]),
                "site_id": self.test_site_id if random.random() < 0.3 else None,  # 30%概率使用同城筛选
                "use_cache": True,
                "refresh_strategy": "append"
            }
            
            # 移除None值
            params = {k: v for k, v in params.items() if v is not None}
            
            # 发送推荐请求
            with self.client.get(
                f"/api/orders/recommend-paginated/{self.test_user_id}",
                params=params,
                catch_response=True,
                name="推荐接口"
            ) as response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if "orders" in data and isinstance(data["orders"], list):
                            response.success()
                            print(f"✅ 推荐接口成功: 用户{self.test_user_id}, 返回{len(data['orders'])}个商单")
                        else:
                            response.failure(f"响应格式错误: {data}")
                    except json.JSONDecodeError:
                        response.failure("响应不是有效的JSON格式")
                else:
                    response.failure(f"HTTP状态码错误: {response.status_code}")
                    
        except Exception as e:
            print(f"❌ 推荐接口测试异常: {str(e)}")
    
    @task(1)  # 权重1：30%的概率提交商单
    def test_submit_interface(self):
        """测试提交接口性能"""
        try:
            # 更新测试数据，确保唯一性
            self.test_order_data["taskNumber"] = f"TEST_{int(time.time())}_{random.randint(100, 999)}"
            self.test_order_data["title"] = f"压力测试商单_{int(time.time())}"
            
            # 发送提交请求
            with self.client.post(
                "/api/orders/submit",
                json=self.test_order_data,
                catch_response=True,
                name="提交接口"
            ) as response:
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if data.get("status") == "success":
                            response.success()
                            print(f"✅ 提交接口成功: 用户{self.test_user_id}, 商单{data.get('task_number')}")
                        else:
                            response.failure(f"提交失败: {data.get('message', '未知错误')}")
                    except json.JSONDecodeError:
                        response.failure("响应不是有效的JSON格式")
                else:
                    response.failure(f"HTTP状态码错误: {response.status_code}")
                    
        except Exception as e:
            print(f"❌ 提交接口测试异常: {str(e)}")
    
    @task(1)  # 权重1：测试推荐池状态
    def test_recommendation_pool_status(self):
        """测试推荐池状态（可选）"""
        try:
            # 测试双推荐池状态
            params = {
                "page": 1,
                "page_size": 5,
                "use_cache": True
            }
            
            with self.client.get(
                f"/api/orders/recommend-paginated/{self.test_user_id}",
                params=params,
                catch_response=True,
                name="推荐池状态检查"
            ) as response:
                if response.status_code == 200:
                    response.success()
                else:
                    response.failure(f"状态检查失败: {response.status_code}")
                    
        except Exception as e:
            print(f"❌ 推荐池状态检查异常: {str(e)}")


# 测试事件监听器
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """测试开始时的回调"""
    print("🚀 压力测试开始！")
    print(f"目标服务器: {environment.host}")
    print(f"并发用户数: {environment.runner.user_count if hasattr(environment.runner, 'user_count') else '动态'}")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """测试结束时的回调"""
    print("🏁 压力测试结束！")
    print("请查看Locust Web界面获取详细性能报告")


# 自定义测试数据生成器
def generate_test_order_data(user_id: str, site_id: str = None) -> Dict[str, Any]:
    """生成测试商单数据"""
    industries = ["软件开发", "设计服务", "营销推广", "技术咨询", "数据分析", "教育培训", "金融服务"]
    
    return {
        "userId": user_id,
        "taskNumber": f"TEST_{int(time.time())}_{random.randint(100, 999)}",
        "title": f"压力测试商单_{random.choice(industries)}_{int(time.time())}",
        "content": f"这是一个用于压力测试的商单，行业：{random.choice(industries)}，时间戳：{int(time.time())}",
        "industryName": random.choice(industries),
        "fullAmount": round(random.uniform(100, 10000), 2),
        "state": "WaitReceive",
        "siteId": site_id or f"site_{random.randint(1, 5)}",
        "priority": random.randint(0, 5),
        "promotion": random.choice([True, False])
    }


if __name__ == "__main__":
    print("📋 Locust压力测试脚本")
    print("使用方法:")
    print("1. 安装依赖: pip install -r requirements_locust.txt")
    print("2. 启动测试: locust -f locustfile.py")
    print("3. 访问Web界面: http://localhost:8089")
    print("4. 配置并发用户数和启动测试")
