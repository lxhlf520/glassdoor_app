import frida, sys, time

DEVICE_ID = "127.0.0.1:16416"

def on_msg(msg, data):
    print("MSG:", msg)

device = frida.get_device(DEVICE_ID, timeout=10)
pid = next((p.pid for p in device.enumerate_processes() if p.name.lower() == "glassdoor"), None)
if pid is None:
    print("Glassdoor not running")
    sys.exit(1)
print(f"attach pid={pid}")
session = device.attach(pid)
script = session.create_script("""
    function probe() {
        send({t: 'probe', Java: typeof Java});
        if (typeof Java !== 'undefined') {
            send({t: 'java', available: Java.available, arch: Process.arch, platform: Process.platform});
            if (Java.available) {
                Java.perform(function(){
                    send({t: 'runtime', version: Java.androidVersion, classes: Java.enumerateLoadedClassesSync().length});
                });
            }
        }
    }
    setTimeout(probe, 3000);
""")
script.on("message", on_msg)
script.load()
time.sleep(6)
print("done")
