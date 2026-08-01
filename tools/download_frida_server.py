import urllib.request, os, sys, lzma

version = "17.15.4"
arch = "android-x86_64"
url = f"https://github.com/frida/frida/releases/download/{version}/frida-server-{version}-{arch}.xz"
out_xz = os.path.join(r"d:\PycharmProjects\AiSpiderProject\glassdoor\tools", f"frida-server-{version}-{arch}.xz")
out_bin = out_xz.replace(".xz", "")

print("downloading", url)
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req, timeout=180) as r, open(out_xz, "wb") as f:
        while True:
            chunk = r.read(8192)
            if not chunk:
                break
            f.write(chunk)
    print("saved xz", out_xz, os.path.getsize(out_xz))
    with lzma.open(out_xz, "rb") as xf, open(out_bin, "wb") as bf:
        bf.write(xf.read())
    print("decompressed", out_bin, os.path.getsize(out_bin))
except Exception as e:
    print("ERROR", e)
    sys.exit(1)
