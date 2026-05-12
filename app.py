"""
电子书搜索后端 — Z-Library 聚合搜索 + 易支付
部署于 Render（美国），直连 Z-Library，无需代理
"""
import re
import time
import logging

import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- 配置 ----------
SEARCH_TIMEOUT = 15

# 搜索引擎列表（按优先级）
SEARCH_SOURCES = [
    {
        "name": "Open Library",
        "url": "https://openlibrary.org/search.json",
    },
    {
        "name": "Google Books",
        "url": "https://www.googleapis.com/books/v1/volumes",
    },
]

# 易支付配置（后续填入）
YIPAY_PID = ""
YIPAY_KEY = ""
YIPAY_API = "https://api.epay.ai/"


# ========== 搜索引擎 ==========

def _search_openlib(query, limit=20):
    """搜索 Open Library JSON API"""
    resp = requests.get(
        "https://openlibrary.org/search.json",
        params={"q": query, "limit": limit, "language": "chi,eng"},
        timeout=SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []

    for doc in data.get("docs", []):
        title = doc.get("title", "未知书名")
        author = ", ".join(doc.get("author_name", ["未知"]))[:80]
        year = str(doc.get("first_publish_year", ""))
        filetype = "?"
        # Open Library 有各种格式
        if doc.get("has_fulltext"):
            filetype = "PDF"
        for fmt_name in doc.get("ebook_access", "").split(","):
            if "pdf" in fmt_name.lower():
                filetype = "PDF"
                break
            if "epub" in fmt_name.lower():
                filetype = "EPUB"
                break

        # 生成下载链接
        cover_id = doc.get("cover_i", "")
        olid = doc.get("edition_key", [""])[0] if doc.get("edition_key") else ""
        url = f"https://openlibrary.org/books/{olid}" if olid else f"https://openlibrary.org/search?q={query}"

        results.append({
            "title": title[:100],
            "author": author,
            "filetype": filetype,
            "filesize": "未知",
            "year": year,
            "url": url,
        })
        if len(results) >= limit:
            break

    return results


def _search_google_books(query, limit=20):
    """搜索 Google Books API"""
    resp = requests.get(
        "https://www.googleapis.com/books/v1/volumes",
        params={"q": query, "maxResults": limit, "langRestrict": "zh-CN,en"},
        timeout=SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    results = []

    for item in data.get("items", []):
        info = item.get("volumeInfo", {})
        title = info.get("title", "未知书名")
        author = ", ".join(info.get("authors", ["未知"]))[:80]
        year = info.get("publishedDate", "")[:4]
        filetype = "EPUB" if info.get("accessInfo", {}).get("epub", {}).get("isAvailable") else "?"

        # Google Books 链接
        url = info.get("infoLink", f"https://books.google.com/?q={query}")

        results.append({
            "title": title[:100],
            "author": author,
            "filetype": filetype,
            "filesize": "未知",
            "year": year,
            "url": url,
        })
        if len(results) >= limit:
            break

    return results


def search_books(query, limit=20):
    """搜索电子书，自动切换数据源"""
    errors = []
    for src in SEARCH_SOURCES:
        try:
            if "openlibrary" in src["url"]:
                results = _search_openlib(query, limit)
            else:
                results = _search_google_books(query, limit)

            if results:
                logger.info(f"搜索成功: {src['name']}, {len(results)} 结果")
                return results
        except Exception as e:
            msg = f"{src['name']}: {type(e).__name__}"
            errors.append(msg)
            logger.warning(msg)
            continue
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
        # 只返回必要字段，减少流量
        books = [
            {
                "title": r["title"],
                "author": r["author"],
                "filetype": r["filetype"],
                "filesize": r["filesize"],
                "year": r["year"],
                "id": str(hash(r["url"])),  # 用 hash 做临时 ID
            }
            for r in results
        ]
        return jsonify({"query": q, "count": len(books), "results": books})
    except Exception as e:
        logger.error(f"搜索异常: {e}")
        return jsonify({"error": f"搜索失败: {str(e)[:200]}"}), 500


@app.route("/api/book/<book_id>")
def api_book_detail(book_id):
    """获取书籍下载链接（付费后调用）"""
    # 暂不实现详情页抓取，先用搜索结果的 URL
    return jsonify({"error": "功能开发中"}), 501


@app.route("/api/health")
def api_health():
    """健康检查 — cron-job 每 5 分钟 ping"""
    return jsonify({"status": "ok", "time": int(time.time())})


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ========== 启动 ==========

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
