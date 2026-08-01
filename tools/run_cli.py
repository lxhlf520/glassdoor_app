import subprocess

cmd = [
    "frida", "-U", "-p", "4897", "-q", "-e",
    "send({java: typeof Java, available: (typeof Java !== 'undefined' && Java.available)})",
]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
print("STDOUT:\n", r.stdout)
print("STDERR:\n", r.stderr[:500])
print("code:", r.returncode)
