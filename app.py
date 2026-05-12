"""
电子书搜索后端 — 通过中继服务器搜索 Z-Library + Open Library 备用
部署于 Render（美国）
"""
import time
import logging

import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- 配置 ----------
RELAY_URL = "http://140.143.165.47:5001"  # 搜索中继（你电脑上的 Z-Library 搜索）
SEARCH_TIMEOUT = 30

# 易支付配置（后续填入）
YIPAY_PID = ""
YIPAY_KEY = ""
YIPAY_API = "https://api.epay.ai/"


# ========== 搜索引擎 ==========

def _search_relay(query, limit=20):
    """通过中继搜索 Z-Library"""
    resp = requests.get(
        f"{RELAY_URL}/search",
        params={"q": query},
        timeout=SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])[:limit]


def _search_openlib(query, limit=20):
    """搜索 Open Library JSON API（中继不可用时备用）"""
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
            "title": title[:100], "author": author,
            "filetype": "?", "filesize": "未知",
            "year": year, "url": url,
        })
        if len(results) >= limit:
            break
    return results


def search_books(query, limit=20):
    """搜索电子书：先中继(Z-Library)，后 Open Library"""
    # 优先中继 (Z-Library)
    try:
        results = _search_relay(query, limit)
        if results:
            logger.info(f"中继搜索成功: {len(results)} 结果")
            return results
    except Exception as e:
        logger.warning(f"中继不可用: {e}")

    # 备用 Open Library
    try:
        results = _search_openlib(query, limit)
        if results:
            logger.info(f"OpenLibrary 备用: {len(results)} 结果")
            return results
    except Exception as e:
        logger.warning(f"OpenLibrary 失败: {e}")

    raise Exception("所有搜索源不可用，请稍后重试")


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
