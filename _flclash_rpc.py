"""Speak FlClashCore JSON-RPC over raw TCP 56355: list proxy groups."""
import socket, json, time


def rpc(sock_file, method, data=None, timeout=6):
    req = {"method": method, "data": data,
           "id": f"{method}#{int(time.time()*1000)}probe"}
    sock_file.write(json.dumps(req) + "\n")
    sock_file.flush()
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = sock_file.readline()
        if not line:
            break
        try:
            msg = json.loads(line)
        except Exception:
            continue
        if msg.get("id") == req["id"]:
            return msg
    return None


s = socket.create_connection(("127.0.0.1", 56355), timeout=5)
f = s.makefile("rw", encoding="utf-8", newline="\n")

resp = rpc(f, "getProxies")
if resp is None:
    print("getProxies: no reply (method may not exist)")
else:
    err = resp.get("error")
    if err:
        print("getProxies error:", err)
    else:
        data = resp.get("data") or {}
        proxies = data.get("proxies", data)
        groups = {n: p for n, p in proxies.items()
                  if isinstance(p, dict) and p.get("type") in
                  ("Selector", "URLTest", "Fallback", "LoadBalance")}
        print("groups:", len(groups))
        for name, g in groups.items():
            if g.get("type") == "Selector":
                print(f"  [{g['type']}] {name} now={g.get('now')} options={len(g.get('all', []))}")
        # save full node list of the biggest selector
        big = max((g for g in groups.values() if g.get("type") == "Selector"),
                  key=lambda g: len(g.get("all", [])), default=None)
        if big:
            with open(r"d:\PycharmProjects\AiSpiderProject\glassdoor\_nodes.json", "w", encoding="utf-8") as out:
                json.dump(big.get("all", []), out, ensure_ascii=False, indent=1)
            print("saved biggest selector nodes:", len(big.get("all", [])))
s.close()
