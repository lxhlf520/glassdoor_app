import frida, time

DEVICE_ID = "127.0.0.1:16416"

def on_msg(msg, data):
    print("MSG:", msg)

device = frida.get_device(DEVICE_ID, timeout=10)
pid = next((p.pid for p in device.enumerate_processes() if p.name.lower() == "glassdoor"), None)
print(f"attach pid={pid}")
session = device.attach(pid)
script = session.create_script("""
    try {
        Java.perform(function(){
            send({java: typeof Java, available: Java.available, version: Java.androidVersion});
        });
    } catch (e) {
        send({error: String(e)});
    }
""")
script.on("message", on_msg)
script.load()
time.sleep(2)
print("done")
