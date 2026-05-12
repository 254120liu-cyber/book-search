"""
搜书中继客户端 v3 — HTTP 轮询模式
每 3 秒向 Render 询问是否有搜索请求，有就搜 Z-Library 返回结果
"""
import os
os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"
os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"

import re
import time
import requests
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup

RENDER = "https://book-search-xs91.onrender.com"
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


def post_result(req_id, results):
    """将搜索结果发回 Render"""
    try:
        requests.post(
            f"{RENDER}/api/relay/result",
            json={"id": req_id, "results": results},
            timeout=10,
        )
    except Exception as e:
        print(f"[上报] 失败: {e}", flush=True)


def main():
    print("=" * 50, flush=True)
    print("搜书中继客户端 v3 (HTTP 轮询)", flush=True)
    print(f"目标: {RENDER}", flush=True)
    print("=" * 50, flush=True)

    fail_count = 0

    while True:
        try:
            resp = requests.get(
                f"{RENDER}/api/relay/ping",
                timeout=10,
            )
            data = resp.json()
            task = data.get("task")

            if task:
                req_id = task["id"]
                query = task["q"]
                print(f"[搜索] #{req_id}: {query}", flush=True)

                results = search_zlib(query)
                print(f"[搜索] #{req_id}: {len(results)} 本", flush=True)
                post_result(req_id, results)

            fail_count = 0
            time.sleep(3)

        except KeyboardInterrupt:
            print("\n退出", flush=True)
            break
        except Exception as e:
            fail_count += 1
            delay = min(fail_count * 2, 30)
            print(f"[错误] {e}，{delay}s 后重试", flush=True)
            time.sleep(delay)


if __name__ == "__main__":
    main()
