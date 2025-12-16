# backend/Dockerfile

# 使用官方 Python 轻量镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 防止 Python 生成 .pyc 文件，并让日志直接输出到控制台
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 复制依赖文件并安装
COPY requirements.txt .
# 使用阿里云镜像源加速安装（可选，国内网络环境推荐）
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 复制后端所有代码
COPY . .

# 暴露端口
EXPOSE 8000

# 启动命令：监听 0.0.0.0 才能被容器外访问
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]