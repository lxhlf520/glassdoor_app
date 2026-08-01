import frida, time

DEVICE_ID = "127.0.0.1:16416"

def on_msg(msg, data):
    print("MSG:", msg)

device = frida.get_device(DEVICE_ID, timeout=10)
print("attach by name Glassdoor")
session = device.attach("Glassdoor")
script = session.create_script("""
    send({java: typeof Java, available: (typeof Java !== 'undefined' && Java.available), platform: Process.platform, arch: Process.arch});
""")
script.on("message", on_msg)
script.load()
time.sleep(2)
print("done")
