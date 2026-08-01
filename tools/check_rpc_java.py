import frida, time

DEVICE_ID = "127.0.0.1:16416"

device = frida.get_device(DEVICE_ID, timeout=10)
pid = next((p.pid for p in device.enumerate_processes() if p.name.lower() == "glassdoor"), None)
print(f"attach pid={pid}")
session = device.attach(pid)
script = session.create_script("rpc.exports.javaAvailable = () => Java.available;", name="java_check", runtime='qjs')
script.load()
print("java_available:", script.exports_sync.java_available())
