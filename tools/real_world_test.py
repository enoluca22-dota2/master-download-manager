"""Real-world downloads through a *running* Master Download Manager (normally
the packaged exe), driven via its local API. Uses public / Creative Commons
targets only:

  * Big Buck Bunny, Sintel (Blender Foundation, CC-BY 3.0)
  * "Official Blender Open Movies" playlist (Blender Foundation, CC-BY)
  * python.org embeddable zip (official download)
  * Debian netinst .torrent (official, freely redistributable)

    python tools/real_world_test.py --port 47911 --token <token> --data <MDM_DATA_DIR>
"""

import argparse
import json
import os
import subprocess
import sys
import time
import zipfile

import requests

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--token", required=True)
parser.add_argument("--data", required=True, help="the app's MDM_DATA_DIR (to read its logs)")
parser.add_argument("--ffprobe", default=os.path.join(os.path.dirname(__file__), "..", "vendor", "ffmpeg", "ffprobe.exe"))
parser.add_argument("--only", default="")
args = parser.parse_args()

BASE = f"http://127.0.0.1:{args.port}"
H = {"X-MDM-Token": args.token}
RESULTS = []
BBB = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
SINTEL = "https://www.youtube.com/watch?v=eRsGyueVLvQ"
PLAYLIST = "https://www.youtube.com/playlist?list=PL6B3937A5D230E335"


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)
    return ok


def api(method, path, **kw):
    r = requests.request(method, BASE + path, headers=H, timeout=kw.pop("timeout", 120), **kw)
    try:
        body = r.json()
    except ValueError:
        body = {}
    return r.status_code, body


def wait_job(job_id, timeout, until=("Completed", "Failed", "Cancelled")):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        _, job = api("GET", f"/downloads/{job_id}")
        if job.get("status") != last:
            last = job.get("status")
            print(f"      job {job_id}: {last} {job.get('message', '')}", flush=True)
        if job.get("status") in until:
            return job
        time.sleep(1)
    return job


def probe(path):
    out = subprocess.run([args.ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                          "-of", "json", path], capture_output=True, text=True, timeout=60).stdout
    return [(s["codec_type"], s["codec_name"]) for s in json.loads(out or "{}").get("streams", [])]


def wanted(name):
    return not args.only or name in args.only.split(",")


def main():
    code, conn = api("GET", "/connection")
    check("app reachable with token", code == 200, conn)

    if wanted("http"):
        url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
        code, job = api("POST", "/downloads", json={"url": url, "format_id": "file", "engine": "http"})
        check("direct HTTP queued", code == 200 and job.get("engine") == "http", job.get("detail", ""))
        job = wait_job(job["id"], 300)
        ok = job.get("status") == "Completed" and job.get("result_path") and zipfile.ZipFile(job["result_path"]).testzip() is None
        check("direct HTTP download completes and the zip is intact", ok, job.get("result_path") or job.get("error_message"))

    if wanted("resume"):
        url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
        # 2 MB/s limit so the pause really lands mid-download on a fast connection
        code, job = api("POST", "/downloads", json={"url": url, "format_id": "file", "engine": "http",
                                                     "options": {"speed_limit_kbps": 2048}})
        jid = job["id"]
        wait_job(jid, 60, until=("Downloading",))
        time.sleep(3)
        api("POST", f"/downloads/{jid}/pause")
        paused = wait_job(jid, 30, until=("Paused", "Completed"))
        at_pause = paused.get("downloaded_bytes")
        check("HTTP download pauses mid-way", paused.get("status") == "Paused" and 0 < (at_pause or 0) < (paused.get("total_bytes") or 0),
              f"{at_pause} of {paused.get('total_bytes')} bytes")
        api("POST", f"/downloads/{jid}/resume")
        done = wait_job(jid, 300)
        size = os.path.getsize(done["result_path"]) if done.get("result_path") else 0
        check("paused HTTP download resumes to the exact size", done.get("status") == "Completed" and size == done.get("total_bytes"),
              f"{size} bytes")

    if wanted("cancel"):
        url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
        _, job = api("POST", "/downloads", json={"url": url, "format_id": "file", "engine": "http",
                                                  "save_dir": os.path.join(os.environ.get("TEMP", "."), "mdm-cancel-test")})
        jid = job["id"]
        wait_job(jid, 60, until=("Downloading",))
        api("POST", f"/downloads/{jid}/cancel")
        job = wait_job(jid, 30)
        folder = job["download_dir"]
        leftovers = [f for f in os.listdir(folder)] if os.path.isdir(folder) else []
        check("cancel removes the partial download", job.get("status") == "Cancelled" and not leftovers, f"{leftovers}")

    if wanted("retry"):
        _, job = api("POST", "/downloads", json={"url": "https://www.python.org/ftp/python/0.0.0/missing.zip",
                                                  "format_id": "file", "engine": "http"})
        job = wait_job(job["id"], 60)
        check("a missing file fails with a readable reason", job.get("status") == "Failed" and "404" in (job.get("error_message") or ""),
              f"{job.get('error_message')} | {job.get('error_hint')}")
        code, again = api("POST", f"/downloads/{job['id']}/retry")
        check("failed download can be retried", code == 200 and again.get("retries") == 1)
        wait_job(job["id"], 60)

    if wanted("detect"):
        code, media = api("POST", "/media/detect", json={"url": BBB}, timeout=180)
        ids = {f["format_id"] for f in media.get("formats", [])}
        check("YouTube detection lists real formats", code == 200 and {"video-360", "video-1080", "mp3-192"} <= ids,
              f"{media.get('title')} — {len(ids)} options")
        log = open(os.path.join(args.data, "logs", "media.log"), encoding="utf-8").read()
        check("no 'missing JavaScript runtime' warning (bundled deno + EJS used)",
              "No supported JavaScript runtime" not in log)

    if wanted("video"):
        code, job = api("POST", "/downloads", json={"url": BBB, "format_id": "video-360", "options": {"container": "mp4"}})
        job = wait_job(job["id"], 900)
        streams = probe(job["result_path"]) if job.get("result_path") else []
        types = {t for t, _ in streams}
        check("YouTube 360p video downloads and is merged (video+audio) to MP4",
              job.get("status") == "Completed" and job["result_path"].endswith(".mp4") and types >= {"video", "audio"},
              f"{os.path.basename(job.get('result_path') or '')} {streams} {job.get('error_message') or ''}")

    if wanted("audio"):
        code, job = api("POST", "/downloads", json={"url": BBB, "format_id": "mp3-128"})
        job = wait_job(job["id"], 900)
        streams = probe(job["result_path"]) if job.get("result_path") else []
        check("audio extraction to MP3 128 kbps", job.get("status") == "Completed" and ("audio", "mp3") in streams,
              f"{os.path.basename(job.get('result_path') or '')} {streams} {job.get('error_message') or ''}")

    if wanted("subtitles"):
        code, media = api("POST", "/media/detect", json={"url": SINTEL}, timeout=180)
        subs = [f for f in media.get("formats", []) if f["kind"] == "subtitle" and not f.get("is_auto")]
        check("Sintel lists uploaded subtitles", code == 200 and len(subs) > 0, f"{len(subs)} languages")
        if subs:
            lang = next((f["lang_code"] for f in subs if f["lang_code"].startswith("en")), subs[0]["lang_code"])
            code, job = api("POST", "/downloads", json={"url": SINTEL, "format_id": f"subtitle-{lang}"})
            job = wait_job(job["id"], 300)
            folder = job.get("download_dir", "")
            srt = [f for f in os.listdir(folder) if f.endswith(".srt")] if os.path.isdir(folder) else []
            check("subtitle downloads as SRT", job.get("status") == "Completed" and srt, f"{srt} {job.get('error_message') or ''}")
            code, job = api("POST", "/downloads", json={"url": SINTEL, "format_id": "video-360", "options": {
                "container": "mkv", "subtitles_langs": [lang], "subtitles_format": "srt", "subtitles_embed": True}})
            job = wait_job(job["id"], 900)
            streams = probe(job["result_path"]) if job.get("result_path") else []
            check("subtitle embedded into the video (MKV)", job.get("status") == "Completed" and any(t == "subtitle" for t, _ in streams),
                  f"{streams} {job.get('error_message') or ''}")

    if wanted("playlist"):
        code, pl = api("POST", "/playlist/detect", json={"url": PLAYLIST}, timeout=180)
        check("playlist lists its items", code == 200 and pl.get("entry_count", 0) >= 3,
              f"{pl.get('playlist_title')} — {pl.get('entry_count')} items")
        code, job = api("POST", "/downloads", json={"url": PLAYLIST, "format_id": "mp3-128", "is_playlist": True,
                                                     "options": {"playlist_items": "1-2"}})
        job = wait_job(job["id"], 1800)
        files = [p for p in os.listdir(os.path.dirname(job["result_path"]))] if job.get("result_path") else []
        check("playlist items 1-2 download in playlist order into a playlist folder",
              job.get("status") == "Completed" and len([f for f in files if f.endswith(".mp3")]) == 2 and
              all(f[:3].isdigit() for f in files if f.endswith(".mp3")),
              f"{files} {job.get('error_message') or ''}")

    if wanted("torrent"):
        url = "https://cdimage.debian.org/debian-cd/current/amd64/bt-cd/debian-13.7.0-amd64-netinst.iso.torrent"
        code, media = api("POST", "/media/detect", json={"url": url}, timeout=120)
        check("Debian .torrent URL is fetched and parsed", code == 200 and media.get("engine") == "torrent",
              f"{media.get('title')} {media.get('formats', [{}])[0].get('filesize_bytes')}")
        code, job = api("POST", "/downloads", json={"url": url, "format_id": "torrent-all", "engine": "torrent"})
        jid = job["id"]
        end = time.time() + 240
        best = None
        while time.time() < end:
            _, j = api("GET", f"/downloads/{jid}")
            best = j
            if (j.get("downloaded_bytes") or 0) > 8 * 1024 * 1024 or j.get("status") in ("Failed", "Completed"):
                break
            time.sleep(2)
        check("real torrent connects to peers and downloads data", (best.get("downloaded_bytes") or 0) > 8 * 1024 * 1024,
              f"{(best.get('downloaded_bytes') or 0) // 1048576} MB, peers={best.get('peers')} seeds={best.get('seeds')} "
              f"speed={best.get('speed_text')} {best.get('error_message') or ''}")
        api("POST", f"/downloads/{jid}/pause")
        paused = wait_job(jid, 30, until=("Paused",))
        check("torrent pauses", paused.get("status") == "Paused")
        api("POST", f"/downloads/{jid}/resume")
        time.sleep(15)
        _, j = api("GET", f"/downloads/{jid}")
        check("torrent resumes", j.get("status") in ("Downloading", "Completed") and (j.get("downloaded_bytes") or 0) >= (paused.get("downloaded_bytes") or 0),
              f"{(j.get('downloaded_bytes') or 0) // 1048576} MB")
        api("POST", f"/downloads/{jid}/cancel")
        wait_job(jid, 30)

    if wanted("auto"):
        code, job = api("POST", "/downloads/auto", json={"url": "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-win32.zip"})
        check("'send link' auto-detects the HTTP engine", code == 200 and job.get("engine") == "http", job.get("engine_reason"))
        wait_job(job["id"], 300)

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} real-world checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
