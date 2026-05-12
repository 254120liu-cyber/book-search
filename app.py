"""
电子书搜索后端 — Z-Library 搜索 + Open Library 备用
部署于 Render（美国），curl_cffi 伪装 Chrome TLS 绕过 Cloudflare
"""
import re
import time
import logging

from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- 配置 ----------
ZLIB_MIRRORS = [
    "https://z-lib.id",
    "https://z-lib.fm",
    "https://z-lib.is",
]
SEARCH_TIMEOUT = 25

# 易支付配置（后续填入）
YIPAY_PID = ""
YIPAY_KEY = ""
YIPAY_API = "https://api.epay.ai/"


# ========== Chrome TLS 伪装层 ==========

def _chrome_get(url, **kwargs):
    """用 curl_cffi 伪装 Chrome 131 TLS 指纹发送 GET 请求"""
    from curl_cffi import requests as curl_requests

    return curl_requests.get(
        url,
        impersonate="chrome131",
        timeout=kwargs.pop("timeout", SEARCH_TIMEOUT),
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
        **kwargs,
    )


# ========== Z-Library 搜索引擎 ==========

def _search_zlib(query, mirror, limit=20):
    """在单个 Z-Library 镜像上搜索"""
    url = f"{mirror}/s/"
    resp = _chrome_get(url, params={"q": query})
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for a in soup.select("a[href*='/book/']"):
        if len(results) >= limit:
            break
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if not title or len(title) < 3:
            continue

        # 找父容器获取详细信息
        parent = a
        for _ in range(6):
            parent = parent.parent
            if parent and len(parent.get_text(strip=True)) > 80:
                break
        info = parent.get_text("\n", strip=True) if parent else ""

        book = _parse_zlib_result(title, href, info, mirror)
        if book:
            results.append(book)

    return results


def _parse_zlib_result(title, href, info, mirror):
    """解析 Z-Library 搜索结果"""
    rest = info.replace(title, "", 1)
    lines = [l.strip() for l in rest.split("\n") if l.strip() and len(l.strip()) > 2]

    author = "未知"
    if lines:
        first = lines[0]
        if not re.search(r"MB|GB|KB|PDF|EPUB|TXT|MOBI|\d{4}", first, re.I):
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

    url = href if href.startswith("http") else mirror + href

    return {
        "title": title,
        "author": author,
        "filetype": filetype,
        "filesize": size,
        "year": year,
        "url": url,
    }


# ========== Open Library 备用搜索 ==========

def _search_openlib(query, limit=20):
    """搜索 Open Library JSON API（备用）"""
    import requests

    resp = requests.get(
        "https://openlibrary.org/search.json",
        params={"q": query, "limit": limit, "language": "chi,eng"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []

    for doc in data.get("docs", []):
        title = doc.get("title", "未知书名")
        author = ", ".join(doc.get("author_name", ["未知"]))[:80]
        year = str(doc.get("first_publish_year", ""))
        olid = doc.get("edition_key", [""])[0] if doc.get("edition_key") else ""
        url = f"https://openlibrary.org/books/{olid}" if olid else ""

        results.append({
            "title": title[:100],
            "author": author,
            "filetype": "?",
            "filesize": "未知",
            "year": year,
            "url": url,
        })
        if len(results) >= limit:
            break

    return results


# ========== 统一搜索入口 ==========

def search_books(query, limit=20):
    """搜索电子书：先 Z-Library，后 Open Library"""
    errors = []

    # 优先 Z-Library（curl_cffi 伪装 Chrome）
    for mirror in ZLIB_MIRRORS:
        try:
            results = _search_zlib(query, mirror, limit)
            if results:
                logger.info(f"Z-Lib 搜索成功: {mirror}, {len(results)} 结果")
                return results
        except Exception as e:
            ename = type(e).__name__
            detail = str(e)[:150]
            msg = f"Z-Lib {mirror}: {ename} - {detail}"
            errors.append(msg)
            logger.warning(msg)

    # 备用 Open Library
    try:
        results = _search_openlib(query, limit)
        if results:
            logger.info(f"OpenLibrary 备用搜索: {len(results)} 结果")
            return results
    except Exception as e:
        errors.append(f"OpenLibrary: {type(e).__name__}")

    raise Exception(" | ".join(errors[:3]))


# ========== Flask API ==========

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "请输入书名或关键词"}), 400
    if len(q) > 200:
        return jsonify({"error": "搜索词过长"}), 400

    try:
        results = search_books(q)
        books = [
            {
                "title": r["title"],
                "author": r["author"],
                "filetype": r["filetype"],
                "filesize": r["filesize"],
                "year": r["year"],
                "id": str(hash(r["url"])),
            }
            for r in results
        ]
        return jsonify({"query": q, "count": len(books), "results": books})
    except Exception as e:
        logger.error(f"搜索异常: {e}")
        return jsonify({"error": f"搜索失败: {str(e)[:200]}"}), 500


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "time": int(time.time())})


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ========== 启动 ==========

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
