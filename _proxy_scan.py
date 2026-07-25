"""Scan common local proxy ports; test which give a working egress."""
import requests

for port in (7897, 7890, 7891, 10809, 10808, 2080, 8888, 1080):
    try:
        r = requests.get("http://www.gstatic.com/generate_204", timeout=4,
                         proxies={"http": f"http://127.0.0.1:{port}",
                                  "https": f"http://127.0.0.1:{port}"})
        print(port, "OK", r.status_code)
    except Exception as e:
        print(port, "fail", type(e).__name__)
