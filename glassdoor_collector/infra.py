"""Glassdoor 采集器公共基础设施

包含：FlClash 节点轮换、TLS 指纹轮换、令牌桶限速、
GraphQL 请求封装。供 parallel.py / modules.py 共用。
"""
import json
import logging
import os
import random
import threading
import time
import uuid

import curl_cffi.requests as curl_requests

from .clash import ClashAPI
from .config import (
    BAN_COOLDOWN,
    FP_POOL,
    FP_ROTATE_AFTER,
    PROXY_URL,
    ROTATE_AFTER,
)

PROXIES = {"http": PROXY_URL, "https": PROXY_URL}

ALIVE_NODES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_alive_nodes.json")

log = logging.getLogger("collector.infra")
_tls = threading.local()


# ---------------------------------------------------------------------------
# 全局令牌桶限速器
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self, rate: float):
        self._rate = rate
        self._min_rate = 0.5
        self._max_rate = 3.0
        self._tokens = rate
        self._capacity = max(rate * 2, 4)
        self._last = time.monotonic()
        self._lock = threading.Lock()
        self._last_429 = 0.0
        self._last_adjust = time.monotonic()

    @property
    def rate(self) -> float:
        return self._rate

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity,
                                   self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(min(wait, 1.0))

    def on_429(self):
        with self._lock:
            now = time.monotonic()
            if now - self._last_429 < 10:
                return
            self._last_429 = now
            old = self._rate
            self._rate = max(self._min_rate, self._rate * 0.7)
            self._capacity = max(self._rate * 2, 2)
            self._tokens = min(self._tokens, self._capacity)
            self._last_adjust = now
        log.warning("429: rate %.2f -> %.2f req/s", old, self._rate)

    def maybe_ramp_up(self):
        with self._lock:
            now = time.monotonic()
            if now - self._last_429 < 300 or now - self._last_adjust < 300:
                return
            old = self._rate
            self._rate = min(self._max_rate, self._rate * 1.15)
            self._capacity = max(self._rate * 2, 4)
            self._last_adjust = now
            if self._rate != old:
                log.info("ramp up: rate %.2f -> %.2f req/s", old, self._rate)


# ---------------------------------------------------------------------------
# FlClash 节点轮换
# ---------------------------------------------------------------------------
class NodeRotator:
    def __init__(self):
        self.api = ClashAPI()
        self.lock = threading.Lock()
        self.nodes = self._load_nodes()
        self.idx = -1
        self.current = self.api.current() if self.api.alive() else None
        if self.current in self.nodes:
            self.idx = self.nodes.index(self.current)
        self.req_count = 0
        self.banned_nodes = {}
        self.banned_ips = {}
        self.last_rotate = 0.0
        self.pending_429 = 0
        self.enabled = bool(self.nodes) and self.api.alive()
        self.pause_until = 0.0  # 429 切节点后全局暂停截止时间

    def _load_nodes(self) -> list:
        nodes = []
        try:
            with open(ALIVE_NODES_FILE, encoding="utf-8") as f:
                nodes = json.load(f)
        except Exception:
            pass
        if not nodes:
            try:
                nodes = ClashAPI().nodes()
            except Exception:
                nodes = []
        random.shuffle(nodes)
        return nodes

    def on_request(self):
        with self.lock:
            self.req_count += 1
            if self.enabled and self.req_count >= ROTATE_AFTER:
                self._rotate_locked("proactive")

    def on_429(self):
        with self.lock:
            if time.time() - self.last_rotate < 10:
                return
            self.pending_429 += 1
            if self.pending_429 < 3:
                log.warning("429 on %s (%d/3), not rotating yet",
                            self.current, self.pending_429)
                return
            self.pending_429 = 0
        self._ban_and_rotate("429")

    def on_403(self):
        self._ban_and_rotate("403")

    def on_ok(self):
        with self.lock:
            self.pending_429 = 0

    def wait_if_paused(self):
        """429 切节点后全局暂停，等新节点生效再发请求。"""
        wait = self.pause_until - time.time()
        if wait > 0:
            time.sleep(wait)

    def _ban_and_rotate(self, reason: str):
        with self.lock:
            now = time.time()
            if now - self.last_rotate < 10:
                return
            if self.current:
                unban = now + BAN_COOLDOWN
                self.banned_nodes[self.current] = unban
                try:
                    eg = self.api.egress_ip(timeout=4)
                    if eg and eg[0]:
                        self.banned_ips[eg[0]] = unban
                except Exception:
                    pass
            self._rotate_locked(reason)

    def _rotate_locked(self, reason: str):
        now = time.time()
        n = len(self.nodes)
        for _ in range(n):
            self.idx = (self.idx + 1) % n
            node = self.nodes[self.idx]
            if now < self.banned_nodes.get(node, 0):
                continue
            eg = self.api.switch_and_wait(node, settle=1.0)
            if not eg:
                self.banned_nodes[node] = now + 300
                continue
            if now < self.banned_ips.get(eg[0], 0):
                self.banned_nodes[node] = self.banned_ips[eg[0]]
                continue
            log.warning("rotate[%s]: req=%d %s -> %s (egress %s %s)",
                        reason, self.req_count, self.current, node, eg[0], eg[1])
            self.current = node
            self.req_count = 0
            self.last_rotate = time.time()
            self.pause_until = time.time() + 5  # 切节点后暂停 5s 等新节点生效
            return
        wake = min(list(self.banned_nodes.values()) + [time.time() + 120])
        wait = max(30, wake - time.time() + 5)
        log.warning("all nodes cooling, sleep %.0fs", wait)
        time.sleep(wait)
        self.req_count = 0


# ---------------------------------------------------------------------------
# TLS 指纹轮换
# ---------------------------------------------------------------------------
class FPRotator:
    def __init__(self, pool, after):
        self.pool = list(pool)
        random.shuffle(self.pool)
        self.after = after
        self.lock = threading.Lock()
        self.idx = -1
        self.count = 0
        self.current = None

    def take(self) -> str:
        with self.lock:
            if self.count == 0 or self.count >= self.after:
                self.idx = (self.idx + 1) % len(self.pool)
                self.current = self.pool[self.idx]
                self.count = 0
                if self.idx == 0:
                    random.shuffle(self.pool)
            self.count += 1
            return self.current


# ---------------------------------------------------------------------------
# 全局实例
# ---------------------------------------------------------------------------
rate_limiter = RateLimiter(rate=3.0)
rotator = NodeRotator()
fp_rotator = FPRotator(FP_POOL, FP_ROTATE_AFTER)


def get_session(fp: str):
    if not hasattr(_tls, "sessions"):
        _tls.sessions = {}
    if fp not in _tls.sessions:
        _tls.sessions[fp] = curl_requests.Session(impersonate=fp)
        _tls.sessions[fp].proxies = PROXIES
    return _tls.sessions[fp]


def headers(operation: str) -> dict:
    return {
        "x-gd-id": str(uuid.uuid4()),
        "x-gd-asst": f"{time.time()}.0",
        "x-gd-operation": operation,
        "gd-csrf-token": "android",
        "x-gd-glassbowl-user": "false",
        "apollographql-client-name": "android",
        "apollographql-client-version": "12.21.0",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 12; PJJ110 Build/V417IR; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/110.0.5481.154 Mobile Safari/537.36 GDDroid/12.21.0"
        ),
        "accept": "multipart/mixed; deferSpec=20220824, application/json",
    }


# ---------------------------------------------------------------------------
# 通用 GraphQL 请求
# ---------------------------------------------------------------------------
def fetch_graphql(operation: str, body: dict, timeout: int = 60) -> tuple[int, dict]:
    """返回 (status, data)。status: 200 ok / -1 永久失败 / 0 网络或 HTTP 错误"""
    # 429 切节点后等待，避免重试风暴打爆新节点
    rotator.wait_if_paused()
    body["extensions"] = {"clientLibrary": {"name": "apollo-kotlin", "version": "4.4.3"}}
    try:
        rotator.on_request()
        fp = fp_rotator.take()
        resp = get_session(fp).post(
            "https://api.glassdoor.com/mobile-graph",
            params={"locale": "zh_CN_#Hans"},
            headers=headers(operation), json=body, timeout=timeout,
        )
        if resp.status_code == 200:
            rotator.on_ok()
            return 200, resp.json()
        if resp.status_code == 429:
            log.warning("429 received! body: %s", resp.text[:200])
            rotator.on_429()
            time.sleep(2)
            return 0, {}
        if resp.status_code == 403:
            body_prefix = resp.text[:1]
            if body_prefix in ("{", "[") or "json" in resp.headers.get("content-type", ""):
                log.info("403 employer-blocked op=%s (JSON)", operation)
                return -1, {}
            log.warning("403 CF challenge op=%s, rotating node", operation)
            rotator.on_403()
            time.sleep(5)
            return 0, {}
        log.warning("HTTP %d op=%s", resp.status_code, operation)
        return 0, {}
    except Exception as e:
        log.warning("req error op=%s: %s", operation, str(e)[:100])
        return 0, {}

