FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# 依赖版本由 uv.lock 固定，requirements.lock.txt 是它的导出结果。
# 安装走阿里云 PyPI 镜像：本机实测直连 PyPI 仅约 19 KB/s，镜像站约 5 MB/s。
ARG PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

COPY requirements.lock.txt ./
RUN uv pip install --system --no-cache -r requirements.lock.txt --index-url ${PIP_INDEX}

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
