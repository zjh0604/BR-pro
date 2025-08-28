# 商单推荐系统部署指南 v1.0.10

## 🆕 v1.0.10 版本更新内容

### ✅ 主要修复和优化
1. **后端商单编码API** - 新增通过backend_order_code的CRUD接口
2. **数据清除功能** - 新增clear_server_data.sh脚本，支持服务器数据清除
3. **模型加载优化** - 优化模型加载逻辑，支持在线模型回退
4. **容器启动优化** - 异步模型检查，避免启动阻塞
5. **API文档更新** - 更新API_Integration_Guide.md，新增接口说明
6. **部署指南更新** - 新增数据清除操作说明

## 🆕 v1.0.9 版本更新内容

### ✅ 主要修复和优化
1. **数据库创建修复** - 修复SQLAlchemy URL格式，确保test.db文件正确创建
2. **环境检测优化** - 修复Windows环境误判为容器环境的问题
3. **模型导入修复** - 确保所有模型正确导入，解决表结构不创建问题
4. **SQLAlchemy 2.0兼容性** - 使用text()函数包装SQL查询
5. **移除手动创建** - 简化代码，移除sqlite3依赖，提高可靠性
6. **镜像大小优化** - 移除不必要的sqlite3包，减少镜像体积

### ✅ v1.0.7 版本更新内容

### ✅ 主要修复和优化
1. **数据库路径修复** - 修复数据库文件路径配置问题
2. **权限问题修复** - 解决容器内用户权限与宿主机不匹配问题
3. **编码问题修复** - 修复entrypoint.sh中的字符编码问题
4. **初始化脚本优化** - 使用python3确保正确的Python版本
5. **验证脚本新增** - 新增verify_deployment.sh自动化验证脚本
6. **部署流程优化** - 完善部署验证和问题排查流程

### ✅ 主要修复和优化
1. **容器启动优化** - 新增 `entrypoint.sh` 脚本，优化容器启动流程
2. **数据持久化优化** - 优化数据目录结构，更好的数据持久化方案
3. **Celery 任务分离** - 将 LLM 分析和监控任务分离到不同的 worker
4. **健康检查优化** - 完善所有服务的健康检查机制
5. **数据初始化优化** - 优化数据库初始化流程，避免重复初始化
6. **网络优化** - 继续优化中国网络环境下的性能

---

## 📋 系统要求

### 硬件要求
- **CPU**: 最低4核，推荐8核以上
- **内存**: 最低8GB，推荐16GB以上
- **存储**: 最低20GB可用空间（包含模型下载缓存）
- **网络**: 稳定的互联网连接（用于千帆API调用和模型下载）

### 软件要求
- **操作系统**: Ubuntu 20.04+ / CentOS 8+ / Debian 11+
- **Docker**: 20.10+ （必须）
- **Docker Compose**: 1.29+ （必须）

### 网络端口
以下端口需要在防火墙中开放：
- **8000**: FastAPI服务
- **5555**: Flower监控界面
- **7474**: Neo4j HTTP（管理界面）
- **7687**: Neo4j Bolt协议
- **6379**: Redis（仅内部访问）

## 🚀 部署步骤

### 1. 准备工作目录和数据
```bash
# 创建工作目录
mkdir -p /opt/business-rec
cd /opt/business-rec

# 创建必要的数据目录
mkdir -p data storage business_vector_db logs cache
```

### 2. 准备数据文件
将合并后的订单数据文件复制到 data 目录：
```bash
# 复制合并后的订单数据文件到数据目录
cp /path/to/your/consolidated_orders.json /opt/business-rec/data/
```

**注意**：如果您有原始的 `orders.json` 和 `user_orders.json` 文件，可以先运行合并脚本：
```bash
# 在本地运行合并脚本（可选）
python consolidate_json_files.py
# 然后将生成的 consolidated_orders.json 复制到服务器
```

### 3. 创建配置文件

#### 3.1 创建 docker-compose.prod.yml
```bash
# 创建 docker-compose.prod.yml 文件
cat > docker-compose.prod.yml << 'EOF'
version: '3.8'

services:
  # Neo4j 图数据库
  neo4j:
    image: neo4j:4.4.28
    container_name: business-neo4j
    environment:
      - NEO4J_AUTH=neo4j/password
      - NEO4J_dbms_memory_heap_initial_size=512m
      - NEO4J_dbms_memory_heap_max_size=1G
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4j_data:/data
    networks:
      - business-net
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "password", "RETURN 1"]
      interval: 30s
      timeout: 10s
      retries: 5

  # Redis 缓存数据库
  redis:
    image: redis:7-alpine
    container_name: business-redis
    ports:
      - "7379:6379"  # 宿主机7379端口映射到容器6379，避免与本地Redis冲突
    volumes:
      - redis_data:/data
    networks:
      - business-net
    restart: unless-stopped
    command: redis-server --appendonly yes --maxmemory 512mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 30s
      timeout: 3s
      retries: 5

  # API 服务
  api:
    image: registry.cn-hangzhou.aliyuncs.com/sohuglobal/businessrec:v1.0.12
    container_name: business-api-prod
    ports:
      - "8000:8000"
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=password
      - QIANFAN_AK=${QIANFAN_AK}
      - QIANFAN_SK=${QIANFAN_SK}
      - SENTENCE_TRANSFORMERS_HOME=/app/models
      - AES_KEY=${AES_KEY}
      - HMAC_KEY=${HMAC_KEY}
    volumes:
      - ./data:/app/data
      - ./storage:/app/storage
      - ./business_vector_db:/app/business_vector_db
      - ./cache:/app/cache
      - ./logs:/app/logs
      - ./text2vec-large-chinese:/app/text2vec-large-chinese
      - model_cache:/app/models
    depends_on:
      neo4j:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - business-net
    restart: always
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/"]
      interval: 30s
      timeout: 10s
      retries: 5

  # Celery Worker - LLM分析队列
  celery-llm-worker:
    image: registry.cn-hangzhou.aliyuncs.com/sohuglobal/businessrec:v1.0.12
    container_name: business-celery-llm-worker-prod
    command: celery -A celery_app worker -l info -c 2 --max-tasks-per-child=50
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=password
      - QIANFAN_AK=${QIANFAN_AK}
      - QIANFAN_SK=${QIANFAN_SK}
      - SENTENCE_TRANSFORMERS_HOME=/app/models
      - AES_KEY=${AES_KEY}
      - HMAC_KEY=${HMAC_KEY}
    volumes:
      - ./data:/app/data
      - ./storage:/app/storage
      - ./business_vector_db:/app/business_vector_db
      - ./cache:/app/cache
      - ./logs:/app/logs
      - ./text2vec-large-chinese:/app/text2vec-large-chinese
      - model_cache:/app/models
    depends_on:
      redis:
        condition: service_healthy
      neo4j:
        condition: service_healthy
    networks:
      - business-net
    restart: always

  # Celery Worker - 监控队列
  celery-monitor-worker:
    image: registry.cn-hangzhou.aliyuncs.com/sohuglobal/businessrec:v1.0.12
    container_name: business-celery-monitor-worker-prod
    command: celery -A celery_app worker -l info -c 2 --max-tasks-per-child=50
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=password
      - QIANFAN_AK=${QIANFAN_AK}
      - QIANFAN_SK=${QIANFAN_SK}
      - SENTENCE_TRANSFORMERS_HOME=/app/models
      - AES_KEY=${AES_KEY}
      - HMAC_KEY=${HMAC_KEY}
    volumes:
      - ./data:/app/data
      - ./storage:/app/storage
      - ./business_vector_db:/app/business_vector_db
      - ./cache:/app/cache
      - ./logs:/app/logs
      - ./text2vec-large-chinese:/app/text2vec-large-chinese
      - model_cache:/app/models
    depends_on:
      redis:
        condition: service_healthy
      neo4j:
        condition: service_healthy
    networks:
      - business-net
    restart: always

  # Flower 监控工具
  flower:
    image: registry.cn-hangzhou.aliyuncs.com/sohuglobal/businessrec:v1.0.12
    container_name: business-flower-prod
    command: celery -A celery_app flower --port=5555
    ports:
      - "5555:5555"
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - QIANFAN_AK=${QIANFAN_AK}
      - QIANFAN_SK=${QIANFAN_SK}
      - SENTENCE_TRANSFORMERS_HOME=/app/models
      - AES_KEY=${AES_KEY}
      - HMAC_KEY=${HMAC_KEY}
    volumes:
      - ./data:/app/data
      - ./storage:/app/storage
      - ./business_vector_db:/app/business_vector_db
      - ./cache:/app/cache
      - ./logs:/app/logs
      - ./text2vec-large-chinese:/app/text2vec-large-chinese
      - model_cache:/app/models
    depends_on:
      - redis
    networks:
      - business-net
    restart: always

  # 数据初始化服务
  init-db:
    image: registry.cn-hangzhou.aliyuncs.com/sohuglobal/businessrec:v1.0.12
    container_name: business-init-db-prod
    entrypoint: ["bash", "-c"]
    command: ["sleep 30 && python3 init_db_from_json.py && python3 init_graph_db.py"]
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=password
      - QIANFAN_AK=${QIANFAN_AK}
      - QIANFAN_SK=${QIANFAN_SK}
      - SENTENCE_TRANSFORMERS_HOME=/app/models
      - AES_KEY=${AES_KEY}
      - HMAC_KEY=${HMAC_KEY}
    volumes:
      - ./data:/app/data
      - ./storage:/app/storage
      - ./business_vector_db:/app/business_vector_db
      - ./cache:/app/cache
      - ./logs:/app/logs
      - ./text2vec-large-chinese:/app/text2vec-large-chinese
      - model_cache:/app/models
    depends_on:
      neo4j:
        condition: service_healthy
      redis:
        condition: service_healthy
    networks:
      - business-net
    restart: "no"

volumes:
  neo4j_data:
  redis_data:
  model_cache:

networks:
  business-net:
    driver: bridge
EOF
```

#### 3.2 创建环境变量文件
```bash
cat > .env << 'EOF'
# 千帆AI平台配置
QIANFAN_AK=your_actual_access_key
QIANFAN_SK=your_actual_secret_key

# AES & HMAC 密钥（必需）
AES_KEY=1234567890123456
HMAC_KEY=your_hmac_secret_key

# 数据库配置
REDIS_HOST=redis
REDIS_PORT=6379
NEO4J_URI=bolt://neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
EOF
```
```bash
QIANFAN_AK=XSJX4K42BrVpcUMCgHY8FlfF
QIANFAN_SK=JFi304xjqzXZPAE1k1087scqbOQ7MiKv

#AES & HMAC 密钥
AES_KEY=1234567890123456
HMAC_KEY=your_hmac_secret_key

```

### 4. 登录阿里云镜像仓库
```bash
# 登录阿里云镜像仓库（必需步骤）
# 如果没有登录，将无法拉取私有镜像
docker login registry.cn-hangzhou.aliyuncs.com

# 输入以下凭据：
# 用户名: yangbin_tb2008
# 密码: sohuglobal123

# 验证登录状态
docker login --get-login registry.cn-hangzhou.aliyuncs.com
```


### 5. 设置目录权限
```bash
# 设置目录权限（确保容器内的应用用户可以访问）
# 容器内appuser的UID/GID为999:999
chown -R 999:999 data storage business_vector_db logs cache
chmod -R 755 data storage business_vector_db logs cache
```

### 6. 启动服务
```bash
# 拉取并启动所有服务
docker-compose -f docker-compose.prod.yml pull
docker-compose -f docker-compose.prod.yml up -d
```

### 7. 监控初始化进度
```bash
# 监控初始化服务日志
docker-compose -f docker-compose.prod.yml logs -f init-db

# 等待看到以下信息：
# - "Database initialization complete"
# - "向量数据库初始化完成"
```

### 8. 验证部署
部署完成后，可以通过以下方式验证服务是否正常运行：

1. **API服务**：
   ```bash
   curl http://localhost:8000/
   ```

2. **Flower监控**：
   在浏览器中访问 `http://your-server-ip:5555`

3. **Neo4j管理界面**：
   在浏览器中访问 `http://your-server-ip:7474`

4. **检查服务状态**：
   ```bash
   docker-compose -f docker-compose.prod.yml ps
   ```

5. **使用验证脚本**（推荐）：
   ```bash
   # 下载验证脚本
   curl -O https://raw.githubusercontent.com/your-repo/business-rec/main/verify_deployment.sh
   chmod +x verify_deployment.sh
   
   # 执行验证
   ./verify_deployment.sh
   ```

5. **查看服务日志**：
   ```bash
   # 查看初始化服务日志
   docker-compose -f docker-compose.prod.yml logs init-db
   
   # 查看API服务日志
   docker-compose -f docker-compose.prod.yml logs api
   
   # 查看LLM Worker日志
   docker-compose -f docker-compose.prod.yml logs celery-llm-worker
   
   # 查看监控Worker日志
   docker-compose -f docker-compose.prod.yml logs celery-monitor-worker
   ```

## 📊 服务访问地址

部署成功后，可以通过以下地址访问服务：

- **API服务**: http://your-server-ip:8000
- **Flower监控**: http://your-server-ip:5555
- **Neo4j管理界面**: http://your-server-ip:7474

## 📁 目录结构说明

```
/opt/business-rec/
├── data/                # 初始化数据文件目录
│   ├── orders.json
│   └── user_orders.json
├── storage/            # 数据库文件存储目录
│   ├── test.db        # SQLite数据库文件
│   └── business_graph.db/  # Neo4j数据库文件
├── business_vector_db/ # 向量数据库文件目录
├── logs/              # 日志文件目录
├── cache/             # 缓存文件目录
├── .env               # 环境变量配置文件
└── docker-compose.prod.yml  # Docker编排配置文件
```

## 🔧 常见问题处理

### 1. 服务无法启动
- 检查目录权限是否正确
- 检查数据文件是否存在且格式正确
- 检查环境变量是否配置正确
- 查看服务日志以获取详细错误信息

### 2. 数据初始化失败
- 确保 data 目录中包含必要的数据文件（orders.json, user_orders.json）
- 检查数据文件格式是否正确
- 查看 init-db 服务的日志：`docker-compose -f docker-compose.prod.yml logs init-db`

### 3. 内存使用过高
- 调整 Neo4j 的内存配置（NEO4J_dbms_memory_heap_max_size）
- 调整 Redis 的最大内存限制（maxmemory）
- 考虑增加服务器内存

### 4. 数据库初始化失败
```bash
# 检查数据库文件是否存在
docker exec -it business-api-prod ls -la /app/storage/test.db

# 手动执行初始化
docker exec -it business-init-db-prod bash
python3 init_db_from_json.py
exit
```

### 5. 权限问题
```bash
# 修复目录权限
sudo chown -R 999:999 /opt/business-rec/storage/
sudo chmod -R 755 /opt/business-rec/storage/

# 重启服务
docker-compose -f docker-compose.prod.yml restart api
```

### 6. 服务不健康
```bash
# 检查服务日志
docker-compose -f docker-compose.prod.yml logs api

# 检查数据库连接
docker exec -it business-api-prod python3 -c "
from storage.db import engine
print('数据库连接测试:', engine)
"
```

## 📋 总体步骤顺序

1. **准备工作目录和数据**
2. **准备数据文件**（orders.json, user_orders.json）
3. **创建配置文件**（docker-compose.prod.yml 和 .env）
4. **登录阿里云镜像仓库**
5. **设置目录权限**
6. **启动服务**
7. **监控初始化进度**
8. **验证部署**

## 🚨 重要提醒

- **数据文件必需**：确保 `data/orders.json` 和 `data/user_orders.json` 文件存在
- **权限设置**：容器内用户UID/GID为999:999，必须正确设置目录权限
- **初始化等待**：系统启动后需要等待数据库初始化完成（约5-15分钟）
- **验证脚本**：建议使用 `verify_deployment.sh` 进行自动化验证

## 🧹 数据清除操作

### 清除所有测试数据

当需要清除服务器上的测试数据时，可以使用以下方法：

#### 方法1：使用清除脚本（推荐）
```bash
# 1. 确保脚本有执行权限
chmod +x clear_server_data.sh

# 2. 执行清除操作
./clear_server_data.sh
```

#### 方法2：手动清除
```bash
# 1. 停止所有服务
docker-compose -f docker-compose.prod.yml down

# 2. 清除数据卷
docker-compose -f docker-compose.prod.yml down -v

# 3. 删除本地数据文件
rm -f ./storage/test.db
rm -rf ./business_vector_db/*
rm -rf ./cache/*
find ./logs -name "*.log" -delete

# 4. 重新启动服务
docker-compose -f docker-compose.prod.yml up -d

# 5. 等待初始化完成
docker-compose -f docker-compose.prod.yml logs -f init-db
```

#### 方法3：使用Python脚本
```bash
# 在服务器上运行
python reset_database.py
```

### 清除范围说明

**会被清除的数据**：
- SQLite数据库中的所有商单、用户、日志
- 向量数据库中的所有向量数据
- Redis缓存中的所有推荐缓存
- Neo4j图数据库中的所有节点和关系
- 应用日志文件

**会被保留的数据**：
- 数据库表结构
- 系统配置文件
- 模型文件（如果挂载了的话）



