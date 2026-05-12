"""
电子书搜索后端 — Render 部署版
PC 通过 HTTP 轮询接收搜索请求 → Z-Library 搜索
PC 不在线时自动降级到 Open Library
"""
import time
import logging
import threading
import uuid

import requests
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- 中继状态 ----------
RELAY_TIMEOUT = 35  # 中继超时（秒）
_pending = {}  # {req_id: {"query": str, "event": Event, "result": list, "ts": float}}
_lock = threading.Lock()
_last_ping = 0  # PC 上次在线时间


# ---------- Open Library 备用 ----------

def _search_openlib(query, limit=20):
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
        results.append({
            "title": title[:100], "author": author,
            "filetype": "?", "filesize": "未知", "year": year,
            "url": f"https://openlibrary.org/books/{olid}" if olid else "",
            "id": str(hash(olid or title)),
        })
        if len(results) >= limit:
            break
    return results


# ---------- PC 中继 API ----------

def _relay_online():
    """PC 是否在线（15秒内有心跳）"""
    return (time.time() - _last_ping) < RELAY_TIMEOUT


@app.route("/api/relay/ping")
def relay_ping():
    """PC 心跳 + 拉取待处理搜索"""
    global _last_ping
    _last_ping = time.time()

    with _lock:
        # 找最早的一个待处理请求
        for req_id, item in list(_pending.items()):
            if item.get("result") is None and item.get("query"):
                query = item.pop("query")
                return jsonify({"task": {"id": req_id, "q": query}})

    return jsonify({"task": None})


@app.route("/api/relay/result", methods=["POST"])
def relay_result():
    """PC 返回搜索结果"""
    data = request.get_json(force=True)
    req_id = data.get("id")
    results = data.get("results", [])

    with _lock:
        if req_id in _pending:
            _pending[req_id]["result"] = results
            _pending[req_id]["event"].set()
            logger.info(f"中继结果 #{req_id}: {len(results)} 本")
            _cleanup_old()
        else:
            logger.warning(f"收到未知请求 #{req_id} （可能已超时）")
    return jsonify({"ok": True})


def _cleanup_old():
    """清理 60 秒以上的旧请求"""
    now = time.time()
    for req_id in list(_pending.keys()):
        if now - _pending[req_id].get("ts", 0) > 60:
            _pending[req_id].setdefault("event", threading.Event()).set()
            _pending.pop(req_id, None)


# ---------- 搜索入口 ----------

def search_books(query, limit=20):
    # 优先通过 PC 中继搜索 (Z-Library)
    if _relay_online():
        req_id = str(uuid.uuid4())[:8]
        event = threading.Event()

        with _lock:
            _pending[req_id] = {
                "query": query, "event": event, "result": None, "ts": time.time()
            }

        # 等待 PC 拉取并返回结果
        if event.wait(timeout=RELAY_TIMEOUT):
            with _lock:
                info = _pending.pop(req_id, {})
            result = info.get("result")
            if result is not None:
                logger.info(f"Z-Library(PC): {len(result)} 结果")
                return result[:limit]
            # result 为空列表说明搜了但没找到——直接返回空
            logger.info(f"Z-Library(PC): 0 结果")
            return []

    # 备用 Open Library
    try:
        results = _search_openlib(query, limit)
        if results:
            logger.info(f"OpenLibrary 备用: {len(results)} 结果")
            return results
        raise Exception("OpenLibrary 返回空结果")
    except Exception as e:
        logger.warning(f"搜索异常: {e}")
        return []


# ---------- Flask API ----------

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
                "title": r["title"], "author": r["author"],
                "filetype": r.get("filetype", "?"), "filesize": r.get("filesize", "未知"),
                "year": r.get("year", ""), "id": r.get("id", str(hash(r.get("url", "")))),
            }
            for r in results
        ]
        return jsonify({"query": q, "count": len(books), "results": books})
    except Exception as e:
        logger.error(f"搜索异常: {e}")
        return jsonify({"error": f"搜索失败: {str(e)[:200]}"}), 500


@app.route("/api/health")
def api_health():
    return jsonify({
        "status": "ok",
        "relay": "online" if _relay_online() else "offline",
        "time": int(time.time()),
    })


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
