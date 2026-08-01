# Glassdoor Collector

Glassdoor APP API 数据采集工具集，支持公司发现、评论采集、福利/面试/岗位采集。

## 功能模块

| 模块 | 命令 | 说明 |
|------|------|------|
| 公司发现 | `glassdoor-discover` | 通过 employerSearchRG API 发现公司 |
| 评论采集 | `glassdoor-collect` | 并行采集公司评论 (8线程, pageSize=100) |
| 模块采集 | `glassdoor-modules` | 采集 Benefits/Interviews/Jobs |
| 数据迁移 | `glassdoor-migrate` | MongoDB → PostgreSQL 迁移 |

## 快速开始

```bash
# 安装依赖
uv sync

# 公司发现
uv run glassdoor-discover

# 并行评论采集
uv run glassdoor-collect

# 模块采集 (benefits + interviews + jobs)
uv run glassdoor-modules --modules benefits,interviews,jobs --workers 4

# MongoDB → PostgreSQL 迁移
uv run glassdoor-migrate
```

## 环境要求

- Python >= 3.11
- MongoDB (本地或远程)
- PostgreSQL (迁移目标)
- FlClash/mihomo 代理 (可选，用于反限流)

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MONGO_URI` | `mongodb://localhost:27017` | MongoDB 连接串 |
| `CLASH_MIXED` | `http://127.0.0.1:7890` | Clash 混合代理端口 |
| `CLASH_BASE` | `http://127.0.0.1:9090` | Clash API 地址 |
| `CLASH_SECRET` | `glassdoor123` | Clash API 密钥 |

## 项目结构

```
glassdoor/
├── glassdoor_collector/   # 核心包
│   ├── __init__.py
│   ├── clash.py           # FlClash 控制器
│   ├── infra.py           # 基础设施 (限速/节点/指纹)
│   ├── collector.py       # 单线程评论采集 (legacy)
│   ├── parallel.py        # 并行评论采集
│   ├── modules.py         # Benefits/Interviews/Jobs
│   ├── discover.py        # 公司发现
│   └── migrate.py         # PG 迁移
├── probes/                # 内部测试脚本
├── tools/                 # APK 逆向工具
├── tests/                 # 测试
├── pyproject.toml
└── README.md
```

## 数据规模

- **公司**: 456,625 家 (MongoDB + PostgreSQL)
- **评论**: 369,930 条 (MongoDB)
- **API**: Glassdoor Android APP GraphQL (`mobile-graph`)

## License

MIT
