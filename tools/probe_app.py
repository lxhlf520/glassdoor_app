import frida, sys

DEVICE_ID = "127.0.0.1:16416"
PACKAGE = "com.glassdoor.app"

def on_msg(msg, data):
    print("MSG:", msg)

device = frida.get_device(DEVICE_ID, timeout=10)
proc = next((p for p in device.enumerate_processes() if p.name.lower() == "glassdoor"), None)
if proc is None:
    print(f"{PACKAGE} not running")
    sys.exit(1)
pid = proc.pid
print(f"attach {PACKAGE} pid={pid}")
session = device.attach(pid)
script = session.create_script("""
    var info = {
        Java: typeof Java,
        javaAvailable: (typeof Java !== 'undefined' && Java.available),
        arch: Process.arch,
        platform: Process.platform,
        pointerSize: Process.pointerSize,
        moduleCount: Process.enumerateModules().length
    };
    send(info);
""")
script.on("message", on_msg)
script.load()
print("done")
