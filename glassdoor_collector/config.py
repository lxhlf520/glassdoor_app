"""Glassdoor Collector 统一配置

所有配置项均支持环境变量覆盖，便于不同环境部署。
使用方式:
    from .config import PG_CONFIG, PARALLEL_WORKERS, ...
"""

import os

# ============================================================================
# PostgreSQL（主存储）
# ============================================================================
PG_CONFIG = {
    "host": os.environ.get("PG_HOST", "localhost"),
    "port": int(os.environ.get("PG_PORT", "5432")),
    "user": os.environ.get("PG_USER", "postgres"),
    "password": os.environ.get("PG_PASSWORD", "long123456"),
    "dbname": os.environ.get("PG_DBNAME", "glassdoor"),
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}

# ============================================================================
# 代理 & 反限流 (infra)
# ============================================================================
PROXY_URL = os.environ.get("CLASH_MIXED", "http://127.0.0.1:7890")

# 隧道代理（设置后自动跳过 Clash 节点轮换，直接使用隧道）
TUNNEL_PROXY_URL = os.environ.get("TUNNEL_PROXY_URL", "")

ROTATE_AFTER = 150              # 每 IP 请求数上限（Clash 模式）
BAN_COOLDOWN = 15 * 60          # 429/403 后节点冷却秒数（Clash 模式）

FP_POOL = [
    "chrome110", "chrome120", "chrome124", "chrome131", "chrome133a",
    "safari15_5", "safari17_0", "safari18_0", "edge101", "firefox133",
    "chrome131_android", "chrome99_android",
]
FP_ROTATE_AFTER = 200           # 低于 ~500-600 指纹配额墙

# ============================================================================
# Glassdoor API 认证
# ============================================================================
GD_ID = "be049dd5-d7f8-4b33-a218-0e7a870d245a"
CF_BM = "SiKUJAdeu0GQgksntu12lJ5yXs90iJ1wUIJepbLTlw4-1784880682.9974203-1.0.1.1-1y.bPFP2UgSt2DRzmBsYEQdIe0KiILc6ZFoQpJdDfVGmmstELMAF8hZMB9Hs_.Q.2KdcVUJ2m6njP1Dx4UD_kfjrNIz0PHtR0k32Szy37ANONXjb51Y6L2jXGA_RPkmm"

# ============================================================================
# 公司发现 (discover)
# ============================================================================
DELAY_BETWEEN_PAGES = 0.3
DELAY_BETWEEN_TERMS = 1.0
NUM_PER_PAGE = 100

SINGLE_LETTER_TERMS = [
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j",
    "k", "l", "m", "n", "o", "p", "q", "r", "s", "t",
    "u", "v", "w", "x", "y", "z",
]
SINGLE_LETTER_MAX_PAGES = 99

DEEP_TERMS = [
    "Software", "Consulting", "Bank", "Insurance", "Hospital",
    "Health", "Tech", "Media", "Finance", "Marketing", "Retail",
    "Energy", "Construction", "Education", "Law",
    "Food", "Transport", "Pharma", "Hotel", "Design", "Security",
    "University", "Capital", "Group", "Partners", "Global",
    "Network", "Service", "Solution", "Digital", "Medical",
    "Investment", "Manufacturing", "Agency", "Property", "Care",
    "Research", "Data", "Cloud", "Systems", "Innovation",
]

MAX_PAGES_PER_TERM = 0          # 0 = 不限
MAX_PAGES_CAP = 500

# ============================================================================
# 旧版单线程采集器 (collector)
# ============================================================================
COLLECTOR_DELAY_PAGES = (0.3, 0.8)
COLLECTOR_DELAY_EMPLOYERS = (1.5, 3)
COLLECTOR_PAGE_SIZE = 20
COLLECTOR_MAX_RETRIES = 3
COLLECTOR_DISCOVERY_PER_PAGE = 100
COLLECTOR_DISCOVERY_MAX_PAGES = 5

SEED_EMPLOYERS = [
    1138,     # Apple Inc.
    1596815,  # DeepMind Technologies Limited
    235798, 321780, 7790, 252593, 18604, 40371,
]

# collector 专用搜索词（与 DEEP_TERMS 不同，更基础）
COLLECTOR_DISCOVERY_TERMS = [
    "in", "er", "an", "on", "at", "es", "en", "or", "al", "ti",
    "te", "ic", "ar", "st", "re", "le", "ra", "li", "io", "nt",
    "ed", "it", "ve", "co", "de", "ri", "ro", "ne", "ma", "ta",
    "si", "el", "la", "ch", "me", "di", "un", "no", "pe", "ac",
    "Software", "Consulting", "Bank", "Insurance", "Hospital",
    "Health", "Tech", "Media", "Finance", "Marketing", "Retail",
    "Energy", "Construction", "Real Estate", "Education", "Law",
    "Food", "Transport", "Pharma", "Hotel", "Design", "Security",
]

# ============================================================================
# 并行采集器 (parallel)
# ============================================================================
PARALLEL_WORKERS = 8
PARALLEL_PAGE_SIZE = 100
PARALLEL_MAX_PAGE_RETRIES = 5
PARALLEL_MAX_PAGES_PER_EMPLOYER = 3000
PARALLEL_QUEUE_SIZE = 0
PARALLEL_FLUSH_SIZE = 500
PARALLEL_STATS_INTERVAL = 30

# ============================================================================
# 模块采集器 (modules)
# ============================================================================
MODULES_WORKERS = int(os.environ.get("MODULES_WORKERS", "20"))
MODULES_FLUSH_SIZE = 200
MODULES_STATS_INTERVAL = 30
MODULES_MAX_PAGES_PER_EMPLOYER = 3000
MODULES_MAX_PAGE_RETRIES = 5


