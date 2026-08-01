import frida, time, sys

runtime = sys.argv[1] if len(sys.argv) > 1 else 'default'
DEVICE_ID = "127.0.0.1:16416"

def on_msg(msg, data):
    print("MSG:", msg)

device = frida.get_device(DEVICE_ID, timeout=10)
pid = next((p.pid for p in device.enumerate_processes() if p.name.lower() == "glassdoor"), None)
print(f"attach pid={pid} runtime={runtime}")
session = device.attach(pid)
script = session.create_script("send({java: typeof Java, runtime: 'chosen'});", runtime=runtime)
script.on("message", on_msg)
script.load()
time.sleep(2)
print("done")
