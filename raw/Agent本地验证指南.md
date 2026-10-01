# Agent 本地验证指南

## 说明

验证 Agent 在模拟运行环境下能否正常运行。本工具提供近似运行环境，辅助参赛 Agent 开发人员验证 Agent 代码。

> 参赛以实际比赛环境运行 Agent 代码的结果为准，本工具不保证与比赛运行环境行为完全一致。

---

## 前置条件

您需要在本地安装以下工具：

*   **Docker**（建议 20.10+）
    
*   **Docker Compose**（如 5.4.0）
    

### macOS Apple Silicon 用户额外要求

由于线上运行环境为 `linux/amd64`，如果您使用的是 ARM 架构的 Mac，需要确保 Docker 能运行 x86\_64 容器。

推荐使用 [colima](https://github.com/abiosoft/colima) 并指定 x86\_64 架构启动：

```bash
# 安装 colima（如果还没装）
brew install colima docker docker-compose

# 以 x86_64 架构启动（分配 4G 内存以匹配线上限制）
colima start --vm-type=qemu --arch x86_64 --cpu 4 --memory 8

```
---

## 您的 Agent 需要满足什么条件

以下是硬性要求，不满足会导致验证失败或线上无法运行：

### 打包格式

*   产物根目录必须命名为 `agent`
    
*   打包为 **ZIP 格式**，大小 **≤ 100MB**
    

### 入口文件（根目录下必须存在其一）

| 语言 | 入口文件 | 说明 |
| --- | --- | --- |
| Python | `agent.py` | — |
| Node.js | `agent.js` | — |
| Java | `agent.jar` | 须在 MANIFEST.MF 中声明 Main-Class |
| Go | `agent` | 编译产物，无后缀 |

### agent.json（必须存在于根目录）

格式如下：

```json
{"runtime": "<python|node|java|go>", "version": "x.y.z"}

```

*   `runtime`：对应您的入口文件语言
    
*   `version`：语义化版本号，如 `1.0.0`
    

### 依赖声明文件（必须存在于根目录）

| 语言 | 文件 |
| --- | --- |
| Python | `requirements.txt` |
| Node.js | `package.json` |
| Java | `pom.xml` |
| Go | `go.mod` |

### 其他要求

*   **所有依赖必须打包在 ZIP 内**，运行环境不提供网络安装依赖的能力
    
*   **必须支持** `**--version**` **参数**：执行后输出与 `agent.json` 中 `version` 字段一致的版本号，退出码为 0
    
*   退出码：0 表示成功，非 0 表示失败
    
*   单次执行超时 **30 分钟**，内存不超过 **4GB**
    
*   网络仅允许访问模型相关服务
    

---

## 目录结构示例

以 Python 为例，标准目录结构如下：

```plaintext
agent/
├── agent.py              # 入口文件
├── agent.json            # 运行时声明
├── requirements.txt      # 依赖声明
├── lib/                  # 依赖包（pip install --target ./lib 的产物）
│   ├── openai/
│   ├── requests/
│   └── ...
└── src/                  # 您的业务代码（可选，按需组织）
    ├── prompt.py
    └── utils.py

```

其中 `agent.json` 内容：

```json
{"runtime": "python", "version": "1.0.0"}

```

`agent.py` 示例片段：

```python
import sys
import os

# 将本地依赖目录加入路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "lib"))

import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--prompt", type=str)
    args = parser.parse_args()

    if args.version:
        print("1.0.0")
        sys.exit(0)

    # 您的业务逻辑
    # ...

if __name__ == "__main__":
    main()

```
---

## 跨平台依赖打包

**为什么需要这一步？**

线上运行环境是 `linux/amd64`（Debian 12）。如果您的开发机是 macOS ARM 或 Windows，直接在本地安装的依赖可能包含平台相关的二进制文件，放到 Linux 上跑不起来。因此需要交叉编译或指定目标平台打包。

各语言的打包命令：

### Python

```bash
pip3 install \
  --platform manylinux2014_x86_64 \
  --python-version 3.12 \
  --target ./lib \
  --only-binary=:all: \
  -r requirements.txt

```
> 如果某些纯 Python 包报找不到 wheel，可以去掉 `--only-binary=:all:` 单独安装那些包。

### Node.js

```bash
npm install --omit=dev --os=linux --cpu=x64

```

### Java

```bash
mvn clean package -DskipTests

```

产物为 fat jar（需要包含所有依赖），放到 `agent/agent.jar`。

### Go

```bash
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o agent

```
---

## 启动验证工具

### 1. 创建工具目录并写入配置文件

新建一个目录，在其中创建 `Dockerfile` 和 `docker-compose.yml` 两个文件，内容见本文档末尾的「Docker 配置参考」章节。

```bash
mkdir agent-local-test-tool && cd agent-local-test-tool
# 创建 Dockerfile，内容见「Docker 配置参考 — Dockerfile」
# 创建 docker-compose.yml，内容见「Docker 配置参考 — docker-compose.yml」

```

### 2. 准备您的 Agent 目录

将您打包好的 agent 目录放到工具目录下，或者通过环境变量指定路径。目录结构应为：

```plaintext
agent-local-test-tool/
├── docker-compose.yml
├── Dockerfile
├── agent/                 # 您的 Agent 目录（或通过 AGENT_DIR 指定）
│   ├── agent.py
│   ├── agent.json
│   └── ...
├── input/                 # 输入数据目录（或通过 INPUT_DIR 指定）
└── output/                # 输出目录（或通过 OUTPUT_DIR 指定）

```

### 3. 配置 API Key

在工具目录下创建 `.env` 文件：

```bash
# 必填：您的 DashScope API Key
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx

# 可选：自定义路径（不填则使用默认值）
# AGENT_DIR=./agent
# INPUT_DIR=./input
# OUTPUT_DIR=./output

# 可选：自定义 Base URL（一般不需要改）
# DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
# OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

```

### 4. 构建镜像并启动

```bash
docker-compose build
docker-compose up

```

容器启动后，Agent 容器的工作目录为 `/workspace`，即您的 Agent 代码所在位置。

---

## 验证步骤

### 第一步：验证 --version 输出

先确认您的 Agent 能正确响应 `--version` 参数：

```bash
docker-compose run --rm agent python agent.py --version

```
> 根据您的语言替换命令，例如：

**预期结果**：输出与 `agent.json` 中 `version` 字段一致的版本号，退出码为 0。

### 第二步：验证完整任务执行

模拟实际运行，传入 prompt 参数：

```bash
docker-compose run --rm agent python agent.py --prompt "请根据 /home/user/ws/input 中的数据完成问答任务，将结果输出到 /home/user/ws/output/result.json"

```

**预期结果**：退出码为 0，且在 output 目录下生成预期产物。

### 第三步：查看产物和日志

```bash
# 查看输出产物
ls ./output/

# 查看运行日志
cat ./logs/agent.log

```

日志文件由您的 Agent 写入 `AGENT_LOG_DIR` 环境变量指定的目录（容器内为 `/home/user/ws/logs`，映射到本地 `./logs`）。

---

## 网络隔离说明

本工具通过 Docker 网络和 Squid 代理模拟线上的网络隔离环境：

*   Agent 容器处于 **internal 网络**，无法直接访问外网
    
*   所有 HTTP/HTTPS 请求通过 `HTTPS_PROXY` 转发到 Squid 代理
    
*   Squid **仅放行** `***.aliyuncs.com**` 域名
    
*   其他所有域名的请求会被拒绝
    

**这意味着**：

*   可以正常调用模型 API（`dashscope.aliyuncs.com`）
    
*   不能访问 PyPI、npm registry、Maven Central 等包管理源——所以依赖必须提前打包
    
*   不能访问 GitHub、Google 等其他网站
    
*   如果您的代码中有访问其他域名的逻辑，会失败
    

---

## Docker 配置参考

以下是本工具使用的 Docker 配置文件，供参考和排查问题使用。

### Dockerfile

```dockerfile
FROM --platform=linux/amd64 python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_VERSION=22.16.0 \
    GO_VERSION=1.22.4

RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/* && \
    sed -i 's|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/*

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates unzip wget xz-utils openjdk-17-jdk \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q "https://registry.npmmirror.com/-/binary/node/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -O /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 && rm /tmp/node.tar.xz

RUN wget -q "https://mirrors.aliyun.com/golang/go${GO_VERSION}.linux-amd64.tar.gz" -O /tmp/go.tar.gz \
    && tar -C /usr/local -xzf /tmp/go.tar.gz && rm /tmp/go.tar.gz \
    && ln -sf /usr/local/go/bin/go /usr/local/bin/go

WORKDIR /workspace

```

### docker-compose.yml

```yaml
services:
  proxy:
    image: ubuntu/squid:latest
    entrypoint: ["/bin/sh", "-c"]
    command:
      - |
        cat > /etc/squid/squid.conf <<'EOF'
        http_port 3128
        acl whitelist dstdomain .aliyuncs.com
        acl SSL_ports port 443
        acl CONNECT method CONNECT
        http_access allow CONNECT SSL_ports whitelist
        http_access deny all
        cache deny all
        EOF
        squid -N -f /etc/squid/squid.conf
    networks:
      intranet:
      internet:

  agent:
    build:
      context: .
      dockerfile: Dockerfile
    platform: linux/amd64
    environment:
      - HTTPS_PROXY=http://proxy:3128
      - HTTP_PROXY=http://proxy:3128
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - DASHSCOPE_BASE_URL=${DASHSCOPE_BASE_URL:-https://dashscope.aliyuncs.com/api/v1}
      - OPENAI_BASE_URL=${OPENAI_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}
      - AGENT_LOG_DIR=/home/user/ws/logs
    volumes:
      - ${AGENT_DIR:-./agent}:/workspace:ro
      - ${INPUT_DIR:-./input}:/home/user/ws/input:ro
      - ${OUTPUT_DIR:-./output}:/home/user/ws/output:rw
      - ${AGENT_LOG_DIR:-./logs}:/home/user/ws/logs:rw
    working_dir: /workspace
    depends_on:
      - proxy
    networks:
      intranet:

networks:
  intranet:
    internal: true
  internet:
    driver: bridge

```
---

## 环境变量一览

| 变量名 | 说明 | 默认值 |
| --- | --- | --- |
| `DASHSCOPE_API_KEY` | 千问 API 密钥（必填） | 无 |
| `DASHSCOPE_BASE_URL` | DashScope API 地址 | `https://dashscope.aliyuncs.com/api/v1\` |
| `OPENAI_BASE_URL` | OpenAI 兼容接口地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1\` |
| `AGENT_LOG_DIR` | 日志输出目录 | `/home/user/ws/logs` |
| `HTTPS_PROXY` | HTTPS 代理地址（自动设置） | `http://proxy:3128\` |
| `HTTP_PROXY` | HTTP 代理地址（自动设置） | `http://proxy:3128\` |

> `HTTPS_PROXY` 和 `HTTP_PROXY` 由容器自动设置，您不需要在 `.env` 中配置。

---

## 清理

验证完成后，停止并清理容器和相关资源：

```bash
# 停止并移除容器
docker-compose down

# 如果需要同时清理构建的镜像
docker-compose down --rmi local

# 清理输出和日志（按需）
# rm -rf ./output/* ./logs/*

```