"""扫描 Clash 所有节点，保活检测后生成 _alive_nodes.json。

在新机器上运行一次，生成存活节点列表供采集器使用。
设置环境变量 CLASH_GROUP 适配不同 selector 名称。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clash_api import ClashAPI, GROUP

SCAN_TIMEOUT = 3000  # 单节点测速超时 (ms)
MIN_ALIVE = 10       # 最少存活节点数

api = ClashAPI()
if not api.alive():
    print("ERROR: Clash external-controller 不可达")
    print(f"  BASE={api.base}")
    print("  请确认 Clash 已启动且启用了 external-controller")
    sys.exit(1)

all_nodes = api.nodes()
print(f"组 {GROUP}: {len(all_nodes)} 节点，正在延迟检测...")

alive = []
dead_cnt = 0
for i, node in enumerate(all_nodes):
    delay = api.delay(node, timeout=SCAN_TIMEOUT)
    if delay is not None:
        alive.append(node)
    else:
        dead_cnt += 1
    if (i + 1) % 20 == 0:
        print(f"  [{i+1}/{len(all_nodes)}] alive={len(alive)} dead={dead_cnt} ...")
    # 达到最少存活数后加快速度（剩余大量死节点时不必全扫描）
    if len(alive) >= MIN_ALIVE and (len(all_nodes) - i - 1) > 100 and i > 50:
        # 前 50 个已经扫出足够存活节点，剩下的大概率也是死的，抽样扫描即可
        # 但仍全量扫描保完整性
        pass

output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_alive_nodes.json")
with open(output, "w", encoding="utf-8") as f:
    json.dump(alive, f, ensure_ascii=False, indent=1)

print(f"\n完成: {len(alive)} 个存活节点 → {output}")
if len(alive) < MIN_ALIVE:
    print(f"WARNING: 存活节点不足 {MIN_ALIVE}，采集器可能无法稳定运行")
