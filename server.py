#!/usr/bin/env python3
"""
搜书神器 v6 — Z-Library JSON API
POST /eapi/book/search 返回结构化数据，100% 可靠
"""
import re
import time
import subprocess
import threading

import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")

# Z-Library eAPI
API_URL = "https://z-lib.fm/eapi/book/search"
PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36",
    "Content-Type": "application/x-www-form-urlencoded",
}

_session = None


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.proxies = PROXY
        _session.headers.update(API_HEADERS)
    return _session


def _api_request(data, max_retries=4):
    """调用 Z-Library API，带重试"""
    for attempt in range(max_retries):
        try:
            s = _get_session()
            r = s.post(API_URL, data=data, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception:
            _session = None  # 重建 session
            if attempt < max_retries - 1:
                time.sleep((attempt + 1) * 1.5)
    return {"books": []}


def search_books(query, limit=25):
    """搜索 Z-Library JSON API"""
    data = {"message": query, "limit": str(limit)}
    result = _api_request(data)

    books = []
    for b in result.get("books", []):
        title = b.get("title", "未知书名")
        author = b.get("author", "未知")
        filetype = b.get("extension", "?").upper()
        filesize = b.get("filesize", "未知")
        if filesize and filesize != "未知":
            try:
                sz = int(filesize)
                if sz > 1024 * 1024:
                    filesize = f"{sz / 1024 / 1024:.1f} MB"
                elif sz > 1024:
                    filesize = f"{sz / 1024:.0f} KB"
                else:
                    filesize = f"{sz} B"
            except ValueError:
                pass
        year = b.get("year", "") or ""
        book_id = b.get("id", "")
        url = f"https://z-lib.fm/book/{book_id}" if book_id else ""

        books.append({
            "title": title, "author": author,
            "filetype": filetype, "filesize": filesize,
            "year": str(year), "url": url,
            "id": str(book_id),
        })
        if len(books) >= limit:
            break

    return books


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q or len(q) > 200:
        return jsonify({"error": "请输入书名"}), 400

    t0 = time.time()
    results = search_books(q)
    dt = time.time() - t0

    books = [{
        "title": r["title"], "author": r["author"],
        "filetype": r["filetype"], "filesize": r["filesize"],
        "year": r["year"], "id": r["id"],
    } for r in results]

    print(f"  {q}: {len(books)} 本 ({dt:.1f}s)", flush=True)
    return jsonify({"query": q, "count": len(books), "results": books})


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


def start_tunnel():
    import os as _os
    try:
        cf = _os.path.join(_os.path.dirname(__file__), "cloudflared.exe")
        proc = subprocess.Popen(
            [cf, "tunnel", "--url", "http://localhost:5000"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for line in proc.stdout:
            line = line.strip()
            if "https://" in line and ".trycloudflare.com" in line:
                url = re.search(r'https://[a-z0-9.-]+\.trycloudflare\.com', line)
                if url:
                    print(f"\n  公网地址: {url.group()}", flush=True)
                    break
    except FileNotFoundError:
        print("  cloudflared.exe 未找到", flush=True)


if __name__ == "__main__":
    print("搜书神器 v6 — Z-Library JSON API", flush=True)
    # 预热
    _get_session()
    print("  会话已就绪", flush=True)
    threading.Thread(target=start_tunnel, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
