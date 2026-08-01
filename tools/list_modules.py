import frida, sys

DEVICE_ID = "127.0.0.1:16416"

def on_msg(msg, data):
    print("MSG:", msg)

device = frida.get_device(DEVICE_ID, timeout=10)
pid = next((p.pid for p in device.enumerate_processes() if p.name.lower() == "glassdoor"), None)
if pid is None:
    print("Glassdoor not running")
    sys.exit(1)
session = device.attach(pid)
script = session.create_script("""
    var mods = Process.enumerateModules();
    send({total: mods.length, arts: mods.filter(m=>m.name.toLowerCase().includes('art')).map(m=>m.name)});
    send({sample: mods.slice(0,20).map(m=>m.name)});
""")
script.on("message", on_msg)
script.load()
