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
AA_MIRRORS = [
    "https://annas-archive.gs",
    "https://annas-archive.se",
    "https://annas-archive.li",
    "https://annas-archive.org",
]
SEARCH_TIMEOUT = 30

# 易支付配置（后续填入）
YIPAY_PID = ""
YIPAY_KEY = ""
YIPAY_API = "https://api.epay.ai/"


# ========== 搜索引擎 ==========

def _session():
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    s.timeout = SEARCH_TIMEOUT
    return s


def _search_aa_mirror(query, mirror, limit=20):
    """在单个 Anna's Archive 镜像上搜索"""
    s = _session()
    url = f"{mirror}/search"
    resp = s.get(url, params={"q": query})
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for a in soup.select("a[href*='/md5/']"):
        if len(results) >= limit:
            break
        title = a.get_text(strip=True)
        href = a.get("href", "")
        if not title or len(title) < 3:
            continue

        parent = a.find_parent("div")
        info = parent.get_text("\n", strip=True) if parent else ""

        author = "未知"
        rest = info.replace(title, "", 1)
        lines = [l.strip() for l in rest.split("\n") if l.strip() and len(l.strip()) > 2]
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

        results.append({
            "title": title,
            "author": author,
            "filetype": filetype,
            "filesize": size,
            "year": year,
            "url": url,
        })

    return results


def search_books(query, limit=20):
    """搜索电子书，自动切换可用数据源"""
    errors = []
    for mirror in AA_MIRRORS:
        try:
            results = _search_aa_mirror(query, mirror, limit)
            if results:
                logger.info(f"搜索成功: {mirror}, {len(results)} 结果")
                return results
        except Exception as e:
            msg = f"{mirror}: {type(e).__name__}"
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
