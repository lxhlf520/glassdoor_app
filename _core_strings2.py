"""Dump ASCII strings near 'groupName' offset in FlClashCore.exe."""
import re

data = open(r"D:\FlClash\FlClashCore.exe", "rb").read()
center = data.find(b"groupName")
window = data[center - 4000: center + 4000]
strings = re.findall(rb"[ -~]{4,}", window)
for s in strings:
    t = s.decode()
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", t) or "/" in t:
        print(t)
