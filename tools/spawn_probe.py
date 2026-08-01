import frida, sys, time

DEVICE_ID = "127.0.0.1:16416"
PACKAGE = "com.glassdoor.app"

def on_msg(msg, data):
    print("MSG:", msg)

device = frida.get_device(DEVICE_ID, timeout=10)
pid = device.spawn([PACKAGE])
print(f"spawned {PACKAGE} pid={pid}")
session = device.attach(pid)
script = session.create_script("""
    function checkJava() {
        var info = {
            Java: typeof Java,
            javaAvailable: (typeof Java !== 'undefined' && Java.available),
            arch: Process.arch,
            moduleCount: Process.enumerateModules().length
        };
        send(info);
        if (typeof Java !== 'undefined' && Java.available) {
            Java.perform(function() {
                send({javaRuntime: Java.androidVersion, classCount: Java.enumerateLoadedClassesSync().length});
            });
        }
    }
    setTimeout(checkJava, 2000);
""")
script.on("message", on_msg)
script.load()
device.resume(pid)
time.sleep(5)
print("done")
