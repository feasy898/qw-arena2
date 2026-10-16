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

# CNB 的 docker daemon 与流水线容器不在同一文件系统，bind mount 拿不到
# 构建产物目录——改为构建期复制（对 Python 运行时行为与官方挂载方式等价）。
COPY agent/ /workspace/
COPY input/ /home/user/ws/input/

WORKDIR /workspace
