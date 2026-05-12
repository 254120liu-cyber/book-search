"""
电子书搜索后端 — Render 部署版
PC 通过 WebSocket 连接，接收 Z-Library 搜索请求
PC 不在线时自动降级到 Open Library
"""
import eventlet
eventlet.monkey_patch()

import time
import logging
import threading

import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit

app = Flask(__name__, static_folder="static")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- 中继状态 ----------
relay_connected = False
relay_lock = threading.Lock()
SEARCH_TIMEOUT = 30

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
            "filetype": "?", "filesize": "未知",
            "year": year, "url": f"https://openlibrary.org/books/{olid}" if olid else "",
            "id": str(hash(olid or title)),
        })
        if len(results) >= limit:
            break
    return results


# ---------- WebSocket 中继 ----------

@socketio.on("connect")
def on_connect():
    global relay_connected
    with relay_lock:
        relay_connected = True
    logger.info("中继已连接 (PC 在线)")
    emit("welcome", {"msg": "connected to Render"})


@socketio.on("disconnect")
def on_disconnect():
    global relay_connected
    with relay_lock:
        relay_connected = False
    logger.info("中继已断开 (PC 离线)")


@socketio.on("search_result")
def on_search_result(data):
    """PC 返回搜索结果，存入等待队列"""
    req_id = data.get("id")
    if req_id and req_id in _pending:
        _pending[req_id]["result"] = data.get("results", [])
        _pending[req_id]["event"].set()


_pending = {}  # {req_id: {"event": threading.Event, "result": None}}


def _relay_search(query, timeout=SEARCH_TIMEOUT):
    """通过 WebSocket 向 PC 中继发送搜索请求"""
    import uuid

    req_id = str(uuid.uuid4())[:8]
    event = threading.Event()
    _pending[req_id] = {"event": event, "result": None}

    socketio.emit("search", {"id": req_id, "q": query})

    if event.wait(timeout=timeout):
        result = _pending.pop(req_id, {}).get("result")
        if result:
            return result
    _pending.pop(req_id, None)
    raise Exception("中继搜索超时")


# ---------- 搜索入口 ----------

def search_books(query, limit=20):
    # 优先 WebSocket 中继 (Z-Library)
    with relay_lock:
        pc_online = relay_connected

    if pc_online:
        try:
            results = _relay_search(query)
            if results:
                logger.info(f"Z-Library(PC): {len(results)} 结果")
                return results[:limit]
        except Exception as e:
            logger.warning(f"中继搜索失败: {e}")

    # 备用 Open Library
    try:
        results = _search_openlib(query, limit)
        if results:
            logger.info(f"OpenLibrary 备用: {len(results)} 结果")
            return results
        raise Exception("OpenLibrary 返回空结果")
    except Exception as e:
        raise Exception(f"所有搜索源不可用: {e}")


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
    pc = "online" if relay_connected else "offline"
    return jsonify({"status": "ok", "relay": pc, "time": int(time.time())})


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


# ========== 启动 ==========

if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, allow_unsafe_werkzeug=True)
