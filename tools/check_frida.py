"""验证 MuMu 模拟器上的 frida-server 连接与 Java bridge 可用性"""
import time

import frida

DEVICE_ID = "127.0.0.1:16416"


def main():
    device = frida.get_device(DEVICE_ID, timeout=10)
    procs = device.enumerate_processes()
    target = next((p for p in procs if p.name == "system_server"), None)
    if target is None:
        target = next(
            (p for p in procs if "launcher" in p.name.lower() or "systemui" in p.name.lower()),
            None,
        )
    if target is None:
        print("no suitable java process found")
        return
    print(f"attach to: {target.pid} {target.name}")
    session = device.attach(target.pid)
    script = session.create_script(
        "send('Java=' + (typeof Java) + ' available=' + (typeof Java !== 'undefined' && Java.available));"
    )
    script.on("message", lambda msg, data: print("MSG:", msg.get("payload")))
    script.load()
    time.sleep(2)
    session.detach()
    print("done")


if __name__ == "__main__":
    main()
