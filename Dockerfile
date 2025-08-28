# 多阶段构建 - 构建阶段（中国网络完全优化版本）
FROM python:3.9-slim AS builder

WORKDIR /app

# 使用阿里云镜像源加速APT软件包下载
RUN sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    echo "deb http://deb.debian.org/debian bookworm main" >> /etc/apt/sources.list && \
    #sed -i 's|http://security.debian.org|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y \
        build-essential \
        wget \
        curl \
        git \
        git-lfs \
        --no-install-recommends && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

# 复制依赖文件
COPY requirements.txt .

# 使用国内pip源安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple/ \
    --trusted-host pypi.tuna.tsinghua.edu.cn

# 生产阶段
FROM python:3.9-slim

# 创建用户并使用国内APT源
RUN groupadd -r appuser && useradd -r -g appuser appuser && \
    sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|http://security.debian.org|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    apt-get install -y \
        curl \
        git \
        git-lfs \
        --no-install-recommends && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

WORKDIR /app

# 从构建阶段复制Python包
COPY --from=builder /usr/local/lib/python3.9/site-packages /usr/local/lib/python3.9/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制应用代码
COPY --chown=appuser:appuser . .

# 复制并设置入口脚本
COPY --chown=appuser:appuser entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# 创建必要的目录并设置权限（移除不需要的目录）
RUN mkdir -p logs cache && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app

# 环境变量
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser
EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"] 