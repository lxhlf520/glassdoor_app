"""Scan FlClashCore.exe for JSON-RPC method name strings."""
import re

data = open(r"D:\FlClash\FlClashCore.exe", "rb").read()
candidates = [b"getProxies", b"changeProxy", b"getDelay", b"healthCheck",
              b"updateConfig", b"getConfig", b"getConnections", b"getProviders",
              b"getTraffic", b"startListener", b"stopListener", b"closeConnections",
              b"groupName", b"proxyName", b"asyncTestDelay"]
for c in candidates:
    idx = data.find(c)
    print(c.decode(), "->", "FOUND at", idx if idx >= 0 else "not found")
