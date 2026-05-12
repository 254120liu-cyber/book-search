"""
搜书中继客户端 — 在你电脑上运行
启动后自动连接 Render WebSocket，接收搜索请求 → 通过 Sakuracat 代理搜 Z-Library → 返回结果
"""
import os
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

import re
import time
import socketio
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

# ---------- 配置 ----------
RENDER_URL = "https://book-search-xs91.onrender.com"
ZLIB_DOMAINS = ["https://z-lib.id", "https://z-lib.fm"]


def search_zlib(query, limit=25):
    """通过代理搜索 Z-Library"""
    for domain in ZLIB_DOMAINS:
        try:
            resp = curl_requests.get(
                f"{domain}/s/",
                params={"q": query},
                proxy="http://127.0.0.1:7897",
                impersonate="chrome131",
                timeout=25,
            )
            if resp.status_code != 200:
                print(f"  [{domain}] HTTP {resp.status_code}", flush=True)
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []

            for a in soup.select("a[href*='/book/']"):
                if len(results) >= limit:
                    break
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if not title or len(title) < 3:
                    continue

                parent = a
                for _ in range(6):
                    parent = parent.parent
                    if parent and len(parent.get_text(strip=True)) > 80:
                        break
                info = parent.get_text("\n", strip=True) if parent else ""
                rest = info.replace(title, "", 1)
                lines = [l.strip() for l in rest.split("\n") if l.strip() and len(l.strip()) > 2]

                author = "未知"
                if lines:
                    first = lines[0]
                    if not re.search(r"MB|GB|KB|\d{4}", first, re.I):
                        author = first[:60]
                    elif len(lines) > 1:
                        author = lines[1][:60]

                filetype = "?"
                for fmt in ["PDF", "EPUB", "MOBI", "AZW3", "DJVU", "TXT", "FB2"]:
                    if fmt.lower() in info.lower():
                        filetype = fmt
                        break

                size = "未知"
                m = re.search(r"(\d+\.?\d*\s*(MB|GB|KB))", info, re.I)
                if m:
                    size = m.group(1).upper()

                year = ""
                m = re.search(r"\b(19\d{2}|20\d{2})\b", info)
                if m:
                    year = m.group(1)

                url = href if href.startswith("http") else domain + href
                results.append({
                    "title": title, "author": author,
                    "filetype": filetype, "filesize": size,
                    "year": year, "url": url, "id": str(hash(url)),
                })

            if results:
                return results
        except Exception as e:
            print(f"  [{domain}] {e}", flush=True)
            continue
    return []


# ---------- WebSocket 客户端 ----------

sio = socketio.Client(reconnection=True, reconnection_delay=5, reconnection_delay_max=30)


@sio.on("connect")
def on_connect():
    print("[WS] 已连接 Render，等待搜索请求...", flush=True)


@sio.on("disconnect")
def on_disconnect():
    print("[WS] 断开，自动重连中...", flush=True)


@sio.on("search")
def on_search(data):
    req_id = data.get("id", "")
    query = data.get("q", "")
    print(f"[搜索] #{req_id}: {query}", flush=True)
    try:
        results = search_zlib(query)
        print(f"[搜索] #{req_id}: 返回 {len(results)} 本", flush=True)
        sio.emit("search_result", {"id": req_id, "results": results})
    except Exception as e:
        print(f"[搜索] #{req_id}: 出错 {e}", flush=True)
        sio.emit("search_result", {"id": req_id, "results": []})


def main():
    print("=" * 50, flush=True)
    print("搜书中继客户端 v2.0", flush=True)
    print(f"目标: {RENDER_URL}", flush=True)
    print("=" * 50, flush=True)
    while True:
        try:
            print("[WS] 连接中...", flush=True)
            sio.connect(RENDER_URL, wait_timeout=10)
            sio.wait()
        except KeyboardInterrupt:
            print("\n中断退出", flush=True)
            break
        except Exception as e:
            print(f"[WS] 失败: {e}，10秒后重试", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
