# 商单推荐系统 - 后端对接指南

## 一、系统对接架构

```
前端用户 → 后端系统 → 商单推荐系统(本系统) → 后端系统 → 前端用户
```

## 二、核心接口

> ** 核心接口说明**: 以下4个接口是系统的主要功能接口

1. **提交商单接口** (`POST /api/orders/submit`) - 推送商单数据并生成推荐
2. **统一推荐接口** (`POST /api/orders/recommend/orders`) - 获取用户推荐结果
3. **删除商单接口** (`DELETE /api/orders/delete/{order_id}`) - 删除失效商单
4. **清除缓存接口** (`DELETE /api/orders/cache/{user_id}`) - 清理用户缓存

## 三、后端需要提供的接口（后端→推荐系统）

### 1. 推送用户商单数据

后端需要在接收到用户商单后，调用我们的接口推送数据。

**接口信息：**
- **URL**: `POST /api/orders/submit`
- **Content-Type**: `application/json`

**请求示例：**
```
POST http://192.168.150.240:31080/api/orders/submit
Content-Type: application/json
```

**请求体**:
```json
{
    "id": "string",                    // 商单ID（必填）
    "userId": "string",                // 用户唯一标识（必填）
    "title": "string",                 // 商单标题（必填）
    "content": "string",               // 商单详细描述（必填）
    "industryName": "string",          // 行业名称（必填）
    "fullAmount": 10000.50,            // 商单金额（可选，支持两位小数）
    "state": "pending",                // 商单状态（可选，默认pending）
    "siteId": "default",               // 站点ID（可选，用于同城匹配，支持字符串类型）
    "priority": 0,                     // 优先级，0-10，默认0
    "taskNumber": "TASK_001",          // 商单编码（可选）
    "promotion": false,                // 推广广场标识（可选，布尔值，默认false）
    "createTime": "2024-01-01T10:00:00", // 创建时间（可选，自动生成）
    "updateTime": "2024-01-01T10:00:00"  // 更新时间（可选，自动生成）
}
```



**响应示例**:
```json
{
    "status": "success",
    "message": "商单提交成功，推荐结果已生成",
    "userId": "12345",
    "orderId": "67890",
    "taskNumber": "TASK_001",
    "bidirectionalMapping": {
        "orderIdToUser": {"67890": "12345"},
        "userToOrders": {"12345": ["67890"]}
    },
    "dualPoolStatus": {
        "normalPoolGenerated": true,
        "promotionalPoolGenerated": true,
        "normalPoolCount": 5,
        "promotionalPoolCount": 2
    }
}
```

**错误响应**:
```json
{
    "detail": "订单数据验证失败，缺少字段: ['title', 'content']"
}
```

**功能说明**:
1. **直接向量化**: 商单不保存到本地数据库，直接插入向量数据库
2. **立即推荐**: 提交后立即计算推荐结果
3. **Redis存储**: 推荐结果保存到后端Redis，支持双向映射
4. **字段验证**: 使用FieldNormalizer进行数据验证和标准化
5. **双推荐池**: 自动分离正常推荐池和推广商单池，分别存储到Redis
6. **推广筛选**: 基于promotion字段自动识别和筛选推广商单

**状态码说明**:
- `200`: 商单提交成功
- `422`: 数据验证失败（缺少必填字段）
- `500`: 服务器内部错误（向量化失败等）

### 2. 删除商单接口

**接口信息：**
- **URL**: `DELETE /api/orders/delete/{order_id}`
- **Content-Type**: `application/json`

**请求示例：**
```
DELETE http://192.168.150.240:31080/api/orders/delete/67890?user_id=12345&force_delete=false
```

**参数**:
- `order_id`: 商单ID（路径参数，必填）
- `user_id`: 用户ID（查询参数，可选，用于权限校验）
- `force_delete`: 是否强制删除（查询参数，可选，默认false）

**功能说明**:
1. **快速锁定**: 通过反向映射快速锁定失效商单影响的用户
2. **Redis清理**: 从Redis中清理用户推荐列表中的失效商单
3. **映射清理**: 清理失效商单ID的反向映射
4. **向量删除**: 从Milvus向量数据库中删除对应的向量数据
5. **缓存失效**: 清理相关用户缓存

**响应示例**:
```json
{
    "status": "success",
    "message": "商单删除成功",
    "order_id": "67890",
    "affected_users": 3,
    "deleted_at": "2024-01-01T10:00:00",
    "note": "该商单已从推荐系统中完全移除，不会再被推荐给任何用户"
}
```

**错误响应**:
```json
{
    "detail": "商单不存在"
}
```

**状态码说明**:
- `200`: 商单删除成功
- `404`: 商单不存在
- `500`: 删除失败（向量数据库操作失败等）

**使用场景**:
- 平台下架商单
- 用户主动删除商单
- 商单状态变更（从WaitReceive变为其他状态）
- 系统维护和清理

## 四、推荐系统提供的接口（推荐系统→后端）

### 1. 统一推荐接口（主要使用）

**接口**: `POST /api/orders/recommend/orders`

**请求示例：**
```
POST http://192.168.150.240:31080/api/orders/recommend/orders
Content-Type: application/json
```

**请求体**:
```json
{
    "userId": "12345",
    "page": 1,
    "pageSize": 10,
    "industryName": "电商",            // 可选：行业筛选
    "amountMin": 1000,                // 可选：最小金额
    "amountMax": 50000,               // 可选：最大金额
    "siteId": "default",              // 可选：站点ID（同城匹配，支持字符串）
    "search": "技术支持",              // 可选：搜索关键词
    "useCache": true,                 // 可选：是否使用缓存
    "refreshStrategy": "append"        // 可选：刷新策略（append/replace）
}
```

**响应示例**:
```json
{
    "orders": [
        {
            "id": 123,
            "taskNumber": "TASK_001",
            "title": "提供图片处理技术支持",
            "industryName": "电商",
            "fullAmount": 5000.0,
            "state": "WaitReceive",
            "createTime": "2024-01-01T10:00:00",
            "siteId": 1001
        }
    ],
    "user_recommendations": {
        "12345": [123, 124, 125],
        "67890": [126, 127]
    },
    "total": 25,
    "page": 1,
    "page_size": 10,
    "is_cached": false,
    "recommendation_type": "quick_generated"
}
```

**关键字段说明**:
- `orders`: 推荐商单列表（只包含必要字段）
- `userRecommendations`: 用户推荐映射（userId -> [id1, id2, id3]）
- `total`: 可推荐商单总数
- `recommendationType`: 推荐类型
  - `quickGenerated`: 快速生成（基于向量相似度）
  - `poolCached`: 推荐池缓存
  - `cached`: 最终推荐缓存

**双推荐池说明**:
系统会自动生成两个推荐池并存储到Redis：
- **正常推荐池**: `normal_recommendations_{user_id}` - 包含所有推荐商单
- **推广商单池**: `promotional_recommendations_{user_id}` - 仅包含推广商单（promotion=true）

### 2. 清除用户缓存

**接口**: `DELETE /api/orders/cache/{user_id}`

**请求示例：**
```
DELETE http://192.168.150.240:31080/api/orders/cache/12345
```

**功能说明**: 清除指定用户的推荐缓存，用于数据更新后刷新推荐结果

## 五、已废弃的接口

> **注意**: 以下接口已废弃或不再推荐使用

### ~~1. 查询用户历史商单~~
- ~~**URL**: `GET /api/orders/user/{user_id}`~~
- ~~**状态**: 已废弃，本地无orders数据库~~

### ~~2. 接受商单~~
- ~~**URL**: `POST /api/orders/accept/{order_id}`~~
- ~~**状态**: 已废弃，本地无orders数据库~~

### ~~3. 退还商单~~
- ~~**URL**: `POST /api/orders/return/{order_id}`~~
- ~~**状态**: 已废弃，本地无orders数据库~~

### ~~4. 根据后端编码查询商单~~
- ~~**URL**: `GET /api/orders/backend-order/{backend_order_code}`~~
- ~~**状态**: 已废弃，本地无orders数据库~~

### ~~5. 根据后端编码更新商单~~
- ~~**URL**: `PUT /api/orders/backend-order/{backend_order_code}`~~
- ~~**状态**: 已废弃，本地无orders数据库~~

### ~~6. 根据后端编码删除商单~~
- ~~**URL**: `DELETE /api/orders/backend-order/{backend_order_code}`~~
- ~~**状态**: 已废弃，本地无orders数据库~~

## 六、老版本接口

> **注意**: 以下接口仍可使用，但功能已被统一推荐接口覆盖，建议使用核心接口

### 1. 分页推荐接口
- **URL**: `GET /api/orders/recommend-paginated/{user_id}`
- **状态**: 功能已被统一推荐接口覆盖

### 2. 异步推荐接口
- **URL**: `GET /api/orders/recommend-async/{user_id}`
- **状态**: 功能已被统一推荐接口覆盖

### 3. 任务状态查询
- **URL**: `GET /api/orders/task-status/{user_id}/{task_id}`
- **状态**: 功能已被统一推荐接口覆盖

### 4. 获取最终推荐结果
- **URL**: `GET /api/orders/final-recommendations/{user_id}`
- **状态**: 功能已被统一推荐接口覆盖

### 5. 混合推荐接口
- **URL**: `GET /api/orders/recommend-hybrid/{user_id}`
- **状态**: 功能已被统一推荐接口覆盖

## 六、双推荐池Redis缓存结构

### Redis键命名规范

系统会自动为每个用户生成两个推荐池，存储在Redis中：

#### 1. 正常推荐池
- **键名**: `normal_recommendations_{user_id}`
- **数据类型**: JSON字符串（商单列表）
- **过期时间**: 3600秒（1小时）
- **内容**: 包含所有推荐商单，包括推广和非推广商单

#### 2. 推广商单池
- **键名**: `promotional_recommendations_{user_id}`
- **数据类型**: JSON字符串（商单列表）
- **过期时间**: 3600秒（1小时）
- **内容**: 仅包含推广商单（promotion=true）

### 缓存数据格式

```json
[
    {
        "id": 123,
        "taskNumber": "TASK_001",
        "title": "提供图片处理技术支持",
        "industryName": "电商",
        "fullAmount": 5000.0,
        "state": "WaitReceive",
        "createTime": "2024-01-01T10:00:00",
        "siteId": "default",
        "promotion": true,
        "similarity_score": 0.85
    }
]
```

### 双推荐池生成时机

1. **提交接口** (`/api/orders/submit`): 提交新商单后自动生成
2. **推荐接口** (`/api/orders/recommend/orders`): 调用推荐接口后自动生成
3. **异步任务**: 通过Celery异步任务在后台生成

### 后端获取双推荐池数据

后端可以直接从Redis获取双推荐池数据：

```python
# 获取正常推荐池
normal_pool = redis_client.get(f"normal_recommendations_{user_id}")

# 获取推广商单池
promotional_pool = redis_client.get(f"promotional_recommendations_{user_id}")

# 解析JSON数据
if normal_pool:
    normal_orders = json.loads(normal_pool)
if promotional_pool:
    promotional_orders = json.loads(promotional_pool)
```
