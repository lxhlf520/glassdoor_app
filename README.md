# Glassdoor Collector

Glassdoor APP API 数据采集工具集，通过逆向 Android APP 的 GraphQL 端点，实现公司发现、评论采集、福利/面试/岗位采集的全链路自动化，数据存储至 PostgreSQL。

## 环境准备

| 依赖 | 说明 |
|------|------|
| Python >= 3.11 | 运行时 |
| PostgreSQL | 主存储（公司/评论/福利/面试/岗位/进度） |
| FlClash / mihomo | 代理节点池 + 自动轮换（可选，无代理时直连） |

## 安装

```bash
cd glassdoor
uv sync
```

`uv sync` 会自动构建 `glassdoor_collector` 包并注册 4 个 CLI 入口：

```
glassdoor-initdb     →  初始化 PostgreSQL 数据库表
glassdoor-discover   →  公司发现
glassdoor-collect    →  并行评论采集
glassdoor-modules    →  模块采集 (benefits/interviews/jobs)
```

## 配置

所有配置集中在 **[glassdoor_collector/config.py](glassdoor_collector/config.py)**，通过环境变量覆盖默认值。

### 数据库

```bash
# PostgreSQL（主存储，所有模块共用）
export PG_HOST="localhost"
export PG_PORT="5432"
export PG_USER="postgres"
export PG_PASSWORD="long123456"
export PG_DBNAME="glassdoor"
```

### 代理（可选）

**隧道代理（推荐，按请求数计费，每请求换 IP）：**
```cmd
set "TUNNEL_PROXY_URL=http://Ub3T81buEu:Rz39uKOMBH@tunnel.proxy.lthttp.com:9001"
```
设置后自动跳过 Clash 节点轮换，限速 20 req/s。

**Clash 代理池（备选）：**
```cmd
set CLASH_MIXED=http://127.0.0.1:7890
set CLASH_BASE=http://127.0.0.1:9090
set CLASH_SECRET=glassdoor123
```
不设置任何代理时，采集器直连 `api.glassdoor.com`。

### 运行参数调整

编辑 [config.py](glassdoor_collector/config.py) 可调整以下关键参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `PARALLEL_WORKERS` | 8 | 评论采集并发数 |
| `PARALLEL_PAGE_SIZE` | 100 | 每页评论数（服务端上限） |
| `PARALLEL_FLUSH_SIZE` | 500 | 每批写入 PostgreSQL 的评论数 |
| `ROTATE_AFTER` | 250 | 同一 IP 最大请求数 |
| `BAN_COOLDOWN` | 15min | 429/403 后节点冷却时间 |
| `MODULES_WORKERS` | 4 | 模块采集并发数 |

## Windows CMD 部署命令

> ⚠️ 远端 Windows Server 使用 CMD，非 PowerShell。环境变量用 `set VAR=value`，赋值含特殊字符时用双引号包裹：`set "VAR=value"`。

### 前置：设置代理 + 拉代码

```cmd
cd C:\Users\sver1\Desktop\lxh\glassdoor_app
git pull
set "TUNNEL_PROXY_URL=http://Ub3T81buEu:Rz39uKOMBH@tunnel.proxy.lthttp.com:9001"
```

### 数据库初始化（首次部署）

```cmd
uv run glassdoor-initdb
```

### 公司发现

```cmd
uv run glassdoor-discover
```

### 评论采集

```cmd
uv run glassdoor-collect
```

### 模块采集

```cmd
:: 全部按顺序跑（benefits → interviews → jobs）
uv run glassdoor-modules --modules all

:: 分模块跑
uv run glassdoor-modules --modules benefits
uv run glassdoor-modules --modules interviews
uv run glassdoor-modules --modules jobs

:: 自定义并发数（默认 20）
uv run glassdoor-modules --modules jobs --workers 10

:: 测试：只采 10 家公司
uv run glassdoor-modules --modules jobs --max-employers 10 --workers 1

:: 采集全部公司（含 reviewCount=0）
uv run glassdoor-modules --modules jobs --all-employers
```

### 测试脚本

```cmd
:: 快速验证各模块 API 是否正常
set "TUNNEL_PROXY_URL=http://Ub3T81buEu:Rz39uKOMBH@tunnel.proxy.lthttp.com:9001"
python _test_modules.py
```

## 完整启动流程

### Step 0：初始化数据库

```cmd
uv run glassdoor-initdb
```

首次部署时执行一次，创建所有表 + 索引。各模块启动时也会自动建表（幂等）。

### Step 1：启动代理（可选）

```cmd
set "TUNNEL_PROXY_URL=http://Ub3T81buEu:Rz39uKOMBH@tunnel.proxy.lthttp.com:9001"
```

### Step 2：导入公司数据

将已有的公司数据导入 PostgreSQL `employers` 表。

### Step 3：公司发现（扩展公司列表）

扫描 Glassdoor APP API 收录的公司，写入 PostgreSQL `employers` 表。

```cmd
uv run glassdoor-discover
```

**执行逻辑：**
- Phase 1：26 个单字母 × 99 页 = 约 23 万条前缀匹配
- Phase 2：41 个行业关键词深挖，补齐单字母漏掉的非英文名公司
- 断点续传：已完成的 (phase, term) 不重复扫描

### Step 4：并行评论采集

以 8 线程并发采集所有有评论的公司的评论。

```cmd
uv run glassdoor-collect
```

**特性：**
- pageSize=100（服务端上限），请求量是旧版的 1/5
- 令牌桶限速（起始 3 req/s），遇到 429 自动降速，正常时自动回升
- 断点续跑：`review_progress` 按 `(employer_id, done_pages)` 记录，Ctrl+C 后重跑不丢进度
- 节点轮换：每 250 请求自动切代理节点，429/403 立即封禁当前节点
- TLS 指纹轮换：每 200 请求切换 curl-cffi impersonate 指纹

**STATS 输出示例：**

```
STATS req=361 fail=0 pages=361 reviews=34545 emp_done=1 |
  3.0 req/s 300 rev/s | q=349713 | limit=3.00 |
  fp=chrome124 | node=🇯🇵 日本6-VIP88a#114 ban=0 | elapsed 0.0h
```

### Step 5：模块采集

采集公司福利评价、面试经验、招聘岗位。

```cmd
:: 全部三个模块
uv run glassdoor-modules --modules all

:: 仅采集面试
uv run glassdoor-modules --modules interviews --workers 4

:: 测试：只采 10 家公司岗位
uv run glassdoor-modules --modules jobs --max-employers 10 --workers 1
```

| 模块 | 数据写入表 | 说明 |
|------|-----------|------|
| benefits | `benefits` | 福利评价 + overview（美国优先，fallback 其他地区） |
| interviews | `interviews` | 面试经验（难度/结果/问题） |
| jobs | `jobs` | 公司热门岗位（SERP 子集，非全量） |

## 数据库表结构

| 表名 | 说明 |
|------|------|
| `employers` | 公司信息 |
| `discovery_progress` | 公司发现进度 |
| `reviews` | 评论数据 |
| `review_progress` | 评论采集进度 |
| `benefits` | 福利评价 |
| `benefits_progress` | 福利采集进度 |
| `interviews` | 面试经验 |
| `interviews_progress` | 面试采集进度 |
| `jobs` | 招聘岗位 |
| `jobs_progress` | 岗位采集进度 |

## 项目结构

```
glassdoor/
├── glassdoor_collector/     # 核心包
│   ├── config.py            # 统一配置（所有参数集中管理）
│   ├── db.py                # PG 连接池 + 表 DDL + initdb CLI
│   ├── __init__.py
│   ├── clash.py             # FlClash 控制器
│   ├── infra.py             # 基础设施（限速/节点/指纹/GraphQL）
│   ├── discover.py          # 公司发现（单字母 + 关键词）
│   ├── parallel.py          # 并行评论采集（推荐）
│   ├── collector.py         # 单线程评论采集（legacy）
│   └── modules.py           # Benefits/Interviews/Jobs
├── pyproject.toml
└── README.md
```

## License

MIT
