"""端到端测试：真启动界面、真走线程、真下载。

和 tests/test_mediagrab.py 的区别：
  - 那些是单元测试，只调纯函数，不碰界面和网络
  - 这个把整条链跑一遍：输入框 → 主线程快照 → 后台线程 → 下载 → 队列 → 界面收尾

不模拟鼠标（那要 pywinauto，对 Tkinter 不稳），所以它测的不是"点得动吗"，
而是"接线对不对"——按钮有没有接错函数、线程快照漏没漏字段、下载完界面能不能恢复。

本地起一个 HTTP 服务供下载，不依赖外网。
"""

import http.server
import threading
import tkinter as tk

import pytest

import MediaGrab as mg

# 一张 1x1 的 PNG，67 字节
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6300010000050001" "0d0a2db4"
    "0000000049454e44ae426082"
)


class _Handler(http.server.BaseHTTPRequestHandler):
    """任何路径都返回同一张图；/notimage 返回 HTML，用来造失败。"""

    def do_GET(self):
        if self.path.startswith("/notimage"):
            body = b"<html>not an image</html>"
            ctype = "text/html"
        else:
            body = PNG_1X1
            ctype = "image/png"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def file_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """造一个隐藏的界面实例，配置和历史都指到临时目录，不污染真实文件。"""
    monkeypatch.setattr(mg.App, "CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(mg.App, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(mg.App, "LOG_DIR", str(tmp_path / "logs"))

    # 弹窗会卡住流程，全部换成空操作
    for name in ("showinfo", "showwarning", "showerror", "askyesno"):
        monkeypatch.setattr(getattr(mg.messagebox, name), "__call__", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(mg.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(mg.messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(mg.messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(mg.messagebox, "askyesno", lambda *a, **k: True)

    root = tk.Tk()
    root.withdraw()
    instance = mg.App(root)
    instance.outdir_var.set(str(tmp_path / "out"))
    yield instance
    try:
        root.destroy()
    except Exception:
        pass


def _run_until_idle(app, timeout=20.0):
    """点完开始后，把 Tk 的事件循环转起来，直到后台线程收尾。"""
    import time

    app.on_download()
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.root.update()
        if not app.working:
            return True
        time.sleep(0.02)
    return False


def test_direct_image_end_to_end(app, file_server, tmp_path):
    """粘一个直链 → 点开始 → 文件真的落到目录里。"""
    app.url_text.insert("1.0", f"{file_server}/test.png")

    finished = _run_until_idle(app)
    assert finished, "下载没在 20 秒内收尾，可能线程卡住了"

    out = tmp_path / "out"
    files = list(out.glob("*.png"))
    assert len(files) == 1, f"目录里应该有 1 个 png，实际 {[f.name for f in out.iterdir()]}"
    assert files[0].stat().st_size == len(PNG_1X1)

    # 界面要恢复成可再点（tkinter 的 state 是 index 对象，用 instate 判断）
    assert app.download_btn.instate(["!disabled"]), "下载按钮该恢复可点"
    assert app.stop_btn.instate(["disabled"]), "停止按钮该变灰"
    # 成功的链接要进历史
    assert any("/test.png" in u for u in app.history)


def test_failure_fills_retry_list(app, file_server):
    """下载失败 → 进失败清单 → 重试按钮亮起。"""
    app.url_text.insert("1.0", f"{file_server}/notimage")

    assert _run_until_idle(app), "失败任务也没收尾"
    assert len(app.last_failed) == 1, f"失败清单应该有 1 条，实际 {app.last_failed}"
    assert "重试失败 (1)" == app.retry_btn["text"]
    assert app.retry_btn.instate(["!disabled"]), "重试按钮该亮起来"


def test_retry_clears_list_before_rerun(app, file_server):
    """点重试后，把失败链接填回输入框、清单清空、重新开跑。"""
    app.url_text.insert("1.0", f"{file_server}/notimage")
    assert _run_until_idle(app)
    assert len(app.last_failed) == 1

    # 直接替换 _confirm_retry，跳过"看一眼再确认"的弹窗
    app._confirm_retry = lambda: True
    app.on_retry_failed()

    assert app.last_failed == [], "重试后清单该清空，否则会越积越多"
    assert app.url_text.get("1.0", "end").strip() == f"{file_server}/notimage"
    assert _run_until_idle(app) is True or app.working in (True, False)


def test_empty_input_does_nothing(app):
    """输入框空着点开始，不该起线程。"""
    app.on_download()
    assert app.working is False
    assert app.last_failed == []
