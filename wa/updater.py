"""Deteksi dan pasang update dari pengembang lewat GitHub Releases."""
import json
import os
import re
import subprocess
import sys
import urllib.request
import zipfile

REPO = "Mazehax/AI-Hazmat"
TAG_PREFIX = "asistenpc-v"          # rilis Asisten PC diberi tag asistenpc-v0.2.0 dst.
ASSET_NAME = "AsistenPC-windows.zip"


def api_url(repo=REPO):
    return "https://api.github.com/repos/%s/releases?per_page=30" % repo



def parse_version(text):
    nums = re.findall(r"\d+", str(text))
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(remote, local):
    return parse_version(remote) > parse_version(local)


def pick_release(releases, prefix=TAG_PREFIX, asset_name=ASSET_NAME):
    """Pilih rilis Asisten PC terbaru (bukan draft/prerelease) yang punya berkas zip."""
    best = None
    for r in releases or []:
        tag = str(r.get("tag_name", ""))
        if not tag.startswith(prefix) or r.get("draft") or r.get("prerelease"):
            continue
        asset = next((a for a in r.get("assets", []) if a.get("name") == asset_name), None)
        if not asset:
            continue
        ver = tag[len(prefix):]
        if best is None or parse_version(ver) > parse_version(best["version"]):
            best = {"version": ver, "url": asset.get("browser_download_url", ""),
                    "size": asset.get("size", 0), "notes": (r.get("body") or "").strip(),
                    "page": r.get("html_url", "")}
    return best


def fetch_releases(timeout=15, opener=None, repo=REPO):
    req = urllib.request.Request(api_url(repo), headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "AsistenPC-updater"})
    op = opener or urllib.request.build_opener()
    with op.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def check_for_update(current_version, fetch=None, repo=REPO):
    """Mengembalikan info rilis bila ada versi lebih baru, jika tidak None. Error jaringan -> None."""
    try:
        rel = pick_release(fetch() if fetch else fetch_releases(repo=repo))
    except Exception:
        return None
    if rel and is_newer(rel["version"], current_version):
        return rel
    return None


def download(url, dest, progress=None, opener=None, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "AsistenPC-updater"})
    op = opener or urllib.request.build_opener()
    with op.open(req, timeout=timeout) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length", 0) or 0)
        got = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress and total:
                progress(got, total)
    return dest


def safe_extract(zip_path, folder):
    """Ekstrak zip dengan menolak path yang keluar dari folder tujuan."""
    root = os.path.realpath(folder)
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            target = os.path.realpath(os.path.join(root, name))
            if target != root and not target.startswith(root + os.sep):
                raise ValueError("Berkas update tidak aman: %s" % name)
        z.extractall(root)
    return root


def find_payload_root(folder, exe_name="AsistenPC.exe"):
    for dirpath, _dirs, files in os.walk(folder):
        if exe_name in files:
            return dirpath
    return None


def build_swap_script(new_dir, app_dir, exe_path, pid, log_path="update.log"):
    """Skrip kecil yang menunggu aplikasi berhenti, menyalin berkas baru (config.json dan data tidak
    ditimpa), lalu menyalakan aplikasi lagi. Semua langkah dicatat ke update.log untuk diagnosis."""
    return (
        "@echo off\r\n"
        "chcp 65001 >NUL\r\n"
        'set "LOG=%(log)s"\r\n'
        'echo [%%date%% %%time%%] mulai, menunggu PID %(pid)d >> "%%LOG%%"\r\n'
        ":wait\r\n"
        'tasklist /FI "PID eq %(pid)d" 2>NUL | find "%(pid)d" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        "  ping -n 2 127.0.0.1 >NUL\r\n"
        "  goto wait\r\n"
        ")\r\n"
        'echo [%%time%%] menyalin berkas >> "%%LOG%%"\r\n'
        'robocopy "%(new)s" "%(app)s" /E /XF config.json /XD data /R:10 /W:2 >> "%%LOG%%" 2>&1\r\n'
        'echo [%%time%%] robocopy selesai, kode %%errorlevel%% >> "%%LOG%%"\r\n'
        'start "" /D "%(app)s" "%(exe)s"\r\n'
        'echo [%%time%%] aplikasi dinyalakan >> "%%LOG%%"\r\n'
        '(goto) 2>NUL & del "%%~f0"\r\n'
    ) % {"pid": pid, "new": new_dir, "app": app_dir, "exe": exe_path, "log": log_path}


def prepare_and_swap(info, app_dir, work_dir, progress=None, download_fn=download):
    """Unduh, ekstrak, tulis skrip penukar, jalankan. Pemanggil harus segera menutup aplikasi."""
    if not getattr(sys, "frozen", False) and not os.environ.get("ASISTENPC_FORCE_UPDATE"):
        raise RuntimeError("Pembaruan otomatis hanya untuk versi .exe. Unduh versi baru secara manual.")
    os.makedirs(work_dir, exist_ok=True)
    zip_path = os.path.join(work_dir, ASSET_NAME)
    download_fn(info["url"], zip_path, progress)
    extract_dir = os.path.join(work_dir, "new")
    if os.path.isdir(extract_dir):
        import shutil
        shutil.rmtree(extract_dir, ignore_errors=True)
    os.makedirs(extract_dir)
    safe_extract(zip_path, extract_dir)
    payload = find_payload_root(extract_dir)
    if not payload:
        raise RuntimeError("Berkas update tidak berisi AsistenPC.exe.")
    exe_path = os.path.join(app_dir, "AsistenPC.exe")
    script = os.path.join(work_dir, "pasang_update.cmd")
    log_path = os.path.join(work_dir, "update.log")
    with open(script, "w", encoding="utf-8", newline="") as f:
        f.write(build_swap_script(payload, app_dir, exe_path, os.getpid(), log_path))
    # CREATE_NO_WINDOW saja (tanpa DETACHED_PROCESS): cmd tetap butuh konsol tersembunyi agar
    # tasklist/ping/robocopy jalan, dan tetap hidup setelah aplikasi ini keluar.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(["cmd.exe", "/d", "/c", script], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return script
