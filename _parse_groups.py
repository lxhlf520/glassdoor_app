"""Parse FlClash runtime config.yaml: list proxy-groups and node counts."""
import re

path = r"C:\Users\13662\AppData\Roaming\com.follow\clash\config.yaml"
text = open(path, encoding="utf-8").read()

# count proxies
proxies_sec = text.split("\nproxies:", 1)[1].split("\nproxy-groups:", 1)[0]
n_nodes = len(re.findall(r'^\s+- client-fingerprint:', proxies_sec, re.M)) or \
          len(re.findall(r'^\s+- name:', proxies_sec, re.M))
print("total nodes:", n_nodes)

# groups
groups_sec = text.split("\nproxy-groups:", 1)[1].split("\nrules:", 1)[0]
for m in re.finditer(r'^\s+- name: "?(.*?)"?$', groups_sec, re.M):
    print("group:", m.group(1))
