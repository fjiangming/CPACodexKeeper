# CPACodexKeeper Web Dashboard

这是 CPACodexKeeper 的独立 Web 监控仪表盘，提供实时 Token 状态、巡检历史可视化以及直观的配置管理功能。与原 CLI 程序完全解耦。

## 快速启动（本地源码运行）

如果你已经在电脑上拉取了完整的项目源码，可以直接使用以下两种方式启动：

### 方式一：Docker Compose（推荐）
```bash
# 在项目根目录执行
docker compose -f docker-compose.web.yml up -d --build
```
启动后访问 http://localhost:8377 即可。

### 方式二：Python 原生启动
```bash
# 1. 安装 Web 端依赖
pip install -r web/requirements.txt
# 2. 启动服务
python -m web.server
```

---

## 生产服务器免源码一键部署（推荐）

现在项目已配置了 **GitHub Actions 自动构建机制**。当你把代码推送到 GitHub 的 `main`（或 `master`）主分支时，系统会自动将最新的 Web 镜像构建并推送到 GitHub 官方的容器仓库 (GHCR) 中。

因此，在你的生产服务器上，你**完全不需要拉取源码**，只需准备 **1 个配置文件** 即可一键启动：

### 1. 准备编排文件

在服务器任意目录下，新建 `docker-compose.yml`：

```yaml
services:
  keeper-web:
    # 替换为你实际的 GitHub 账号/仓库名，注意名字必须全小写
    image: ghcr.io/你的github账号/cpacodexkeeper-web:main
    container_name: keeper-web
    restart: unless-stopped
    ports:
      - "8377:8377"
    volumes:
      # 持久化存储目录，.env 文件和运行数据都会自动保存在这里
      - keeper_web_data:/app/web/data
    environment:
      CPA_WEB_PORT: 8377
      CPA_WEB_HOST: 0.0.0.0
      CPA_WEB_PASSWORD: your-password   # leave empty to disable auth

volumes:
  keeper_web_data:
```

### 2. 一键拉取并启动

在 `docker-compose.yml` 所在的目录执行：

```bash
# 拉取最新镜像并后台启动
docker compose up -d
```

---

### 3. 如何更新与重启镜像

后续如果有代码更新，由于配置被持久化在了数据卷中，你可以毫无顾忌地拉取最新镜像。

在 `docker-compose.yml` 所在的目录执行以下两行命令即可完成无缝升级：

```bash
# 1. 拉取远端最新镜像
docker compose pull

# 2. 重新创建并启动容器（自动使用新镜像，原有配置数据完全保留）
docker compose up -d
```

## 访问认证

公网部署时**强烈建议**启用访问认证，防止扫描器和未授权用户访问仪表盘。

### 启用方式

只需设置 `CPA_WEB_PASSWORD` 即可，支持两种方式：

- **环境变量**（推荐 Docker 部署）：在 `docker-compose.yml` 的 `environment` 中设置
- **.env 文件**：在持久化数据卷中的 `.env` 文件里设置，或通过 Web 页面的「配置管理」修改

设置后重启服务生效。访问任意页面都会被拦截并跳转到登录页，输入正确密码后方可进入仪表盘。

### 安全机制

- 未认证时所有页面和 API 均不会暴露任何业务信息
- 登录状态通过 HMAC-SHA256 签名的 HttpOnly Cookie 维持，有效期 7 天
- 服务重启后所有旧会话自动失效，需重新登录
- 不设置密码（留空）则不启用认证，向后兼容本地开发场景

---

## 巡检历史与动作明细

每次完整巡检结束后，Web 会在运行数据中记录本轮摘要和结构化动作列表。页面中的“巡检历史明细”支持查看：

- 删除账号列表
- 禁用账号列表
- 启用账号列表
- 刷新账号列表

如果本轮是演练模式，页面会显示“拟删除”“拟禁用”等动作，表示 CLI dry-run 判断出的计划动作，不代表已经实际修改 CPA 端数据。

历史数据默认保存到 Web 运行数据目录中的 `history.json`，最多保留最近 100 次巡检记录。

启动后，系统会自动在持久化数据卷中为你生成一个默认的 `.env` 配置文件（这样以后哪怕你重新拉取镜像、删除重建容器，配置**都不会**丢失被清空）。

启动完成后，通过浏览器访问 `http://服务器IP:8377`，在页面右上角的“配置管理”中填入你的真实 CPA 接口地址和 Token 并保存即可！

---