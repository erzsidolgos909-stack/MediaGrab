#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MediaGrab — 图片 / 视频下载器（图形界面版）

双击桌面「媒体下载器」即可运行。支持三类链接：
  1. 图站 / 相册 / 用户主页（gallery-dl）：Pixiv、Twitter(X)、Instagram、
     Reddit、Pinterest、微博、Bilibili 相簿等上千个站点，批量抓全部图片；
  2. 直接图片链接（requests）：如 https://example.com/photo.jpg；
  3. 视频 / 音频（yt-dlp）：YouTube、B站、TikTok 等，可选清晰度和 MP4/MP3。

功能：多链接（每行一个）、自定义目录、跳过已存在文件、历史去重、代理端口、
cookies、并发下载、实时进度、停止、完成提示音。

v2.0（2026-10-20）相对 v1 的改动：
  * 后台线程不再读取 Tk 控件（v1 在子线程里调 StringVar.get()，会崩）
  * 修正 gallery-dl 返回值判断（run() 返回整数状态码，不是 .total/.success）
  * 后台线程无论怎么结束都必定收尾，界面不会卡死在「下载中」
  * 新增「停止」按钮；直链下载失败不再留半截文件
"""

import json
import logging
import os
import queue
import re
import socket
import sys
import webbrowser
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

try:
    import gallery_dl
    from gallery_dl import config as gd_config
    from gallery_dl import job as gd_job
    HAS_GALLERYDL = True
except ImportError:
    HAS_GALLERYDL = False
    gallery_dl = None
    gd_config = gd_job = None

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    requests = None

HAS_YTDLP = False
yt_dlp = None
try:
    import yt_dlp
    from yt_dlp.utils import DownloadError as YTDL_DownloadError
    HAS_YTDLP = True
except ImportError:
    YTDL_DownloadError = Exception


APP_VERSION = "2.3.7"

# 更新检查：填一个返回下面这种 JSON 的地址就能用；留空则「检查更新」只报未配置。
#   {"version": "2.3.2",
#    "url": "https://.../MediaGrab-2.3.2-安装包.exe",
#    "note": "补了失败重试和自动更新"}
# 放哪都行：GitHub Release 的 raw、Gitee、自己的服务器。改这个常量要重新打包。
UPDATE_URL = "https://gitee.com/mapjey/mediagrab/raw/master/version.json"
UPDATE_TIMEOUT = 4

QUALITY_HEIGHTS = {"1080p": 1080, "720p": 720, "480p": 480}

# 界面显示名 -> yt-dlp / gallery-dl 认的浏览器代号
BROWSER_CHOICES = ["不用", "Chrome", "Edge", "Firefox", "Brave", "Chromium"]
BROWSER_KEYS = {
    "Chrome": "chrome",
    "Edge": "edge",
    "Firefox": "firefox",
    "Brave": "brave",
    "Chromium": "chromium",
}

# 「自动」模式靠这张表判断该走 yt-dlp 还是 gallery-dl。
# 为什么不用 yt-dlp 的 extractor 自动判断：它的 Generic extractor 匹配一切，
# 排掉 Generic 后像 youtu.be 这种短链反而不认；而且 Instagram 这类图站会被它认领，
# 图集会变成"只下一次"。所以宁可用一张能看懂、能手动加的表。
VIDEO_URL_HINTS = (
    "/watch?v=",
    "youtube.com/shorts",
    "youtu.be/",
    "bilibili.com/video",
    "/video/",
    "tiktok.com",
    "/reel/",
    "douyin.com",      # 抖音：短链 v.douyin.com、分享页 modal_id 都靠这条
    "kuaishou.com",    # 快手
    "ixigua.com",      # 西瓜视频
)


def is_video_url(url):
    """粗判一个链接该不该交给 yt-dlp。判错也能靠后面失败回退，但别乱判。"""
    low = (url or "").lower()
    # TikTok 的 /photo/ 是图片帖（滑图 + 背景音乐）。yt-dlp 的 TikTok 解析器
    # 只认 /video/，遇到 /photo/ 直接 Unsupported URL；gallery-dl 能正常下。
    if "/photo/" in low:
        return False
    # Instagram：先给 yt-dlp。它走移动端接口，同一份 cookies 能拿到数据；
    # gallery-dl 的 Instagram 支持从 2026 年起整体失效（一律被踢回登录页）。
    # 纯图片帖 yt-dlp 会报 "No video formats found"，这时由上层回退到 gallery-dl。
    if "instagram.com" in low:
        return True
    return any(k in low for k in VIDEO_URL_HINTS)


class DownloadFailed(Exception):
    """下载最终失败。"""


class UnsupportedUrl(Exception):
    """URL 无效或不受支持。"""


class Cancelled(Exception):
    """用户点了停止。"""


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif", ".svg", ".ico", ".tif", ".tiff")

EXT_BY_CTYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/x-icon": ".ico",
    "image/tiff": ".tif",
}

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# --------------------------------------------------------------------------- #
# 更新检查（只比对版本号，不自动替换 EXE——那需要外部更新器，为一个自用工具不值）
# --------------------------------------------------------------------------- #
def version_tuple(v):
    """"2.3.0" -> (2, 3, 0)；解析不了返回空元组，让调用方当"不知道"处理。"""
    try:
        parts = str(v).strip().lstrip("vV").split(".")
        nums = [int(p) for p in parts[:3]]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums)
    except (ValueError, AttributeError):
        return ()


def is_newer(remote, current=None):
    """远端版本是否比本机新。任一边解析不出来就返回 False——宁可漏报，不要误报。"""
    current = current or APP_VERSION
    rt, ct = version_tuple(remote), version_tuple(current)
    if not rt or not ct:
        return False
    return rt > ct


def fetch_update_info(url=None, timeout=UPDATE_TIMEOUT):
    """请求更新清单。任何失败都返回 None，绝不抛——检查更新不该让程序出问题。"""
    url = url if url is not None else UPDATE_URL
    if not url or not HAS_REQUESTS:
        return None
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data.get("version"):
            return None
        return {
            "version": str(data["version"]),
            "url": str(data.get("url") or ""),
            "note": str(data.get("note") or ""),
        }
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def human_size(num):
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024.0 or unit == "TB":
            return f"{num:.2f} {unit}"
        num /= 1024.0


def clean_url(url):
    """去掉常见跟踪参数（utm_*、srtk、cplk 等），保留路径和必要签名参数。"""
    url = (url or "").strip()
    if "?" not in url:
        return url
    base, query = url.split("?", 1)
    kept = []
    for pair in query.split("&"):
        key = pair.split("=", 1)[0].lower()
        if key.startswith("utm_") or key in ("srtk", "cplk", "ref_id", "ref", "source", "from"):
            continue
        kept.append(pair)
    return base + ("?" + "&".join(kept) if kept else "")


def validate_url(url):
    url = clean_url(url)
    if not url:
        raise ValueError("空链接")
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"无效的链接（需以 http:// 或 https:// 开头）：{url}")
    return url


def is_direct_image_url(url):
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    return path.endswith(IMAGE_EXTS)


def _brief(e):
    s = str(e).strip()
    return s.splitlines()[0] if s else e.__class__.__name__


def _classify(e):
    """把 gallery-dl 抛出的异常分类：no_extractor / not_found / network。"""
    msg = str(e).lower()
    if any(k in msg for k in ("no suitable extractor", "unsupported url", "unsupported")):
        return "no_extractor"
    if any(k in msg for k in ("404", "not found", "deleted", "removed",
                              "does not exist", "unavailable", "invalid url")):
        return "not_found"
    return "network"


def _rc_message(rc):
    """gallery-dl 的 run() 返回整数状态码（位掩码），翻译成人话。"""
    parts = []
    if rc & 1:
        parts.append("部分文件失败或链接不受支持")
    if rc & 4:
        parts.append("被中止")
    if rc & 128:
        parts.append("网络或磁盘错误")
    if not parts:
        parts.append("未知错误")
    return f"gallery-dl 状态码 {rc}（{'；'.join(parts)}）"


def _derive_filename(resp, url, ctype):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*?=(?:UTF-8''|utf-8'')?[\"']?([^\"';]+)", cd, re.I)
    if m:
        name = m.group(1).strip().strip('"')
    else:
        name = url.split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
        try:
            name = requests.utils.unquote(name)
        except Exception:
            pass
    if not name or "." not in name:
        ext = EXT_BY_CTYPE.get(ctype, ".jpg")
        name = "image_" + time.strftime("%Y%m%d_%H%M%S") + ext
    name = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", name).strip(" .")
    if not name:
        name = "image_" + time.strftime("%Y%m%d_%H%M%S") + ".jpg"
    return name


def _unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 1
    while os.path.exists(f"{base}({i}){ext}"):
        i += 1
    return f"{base}({i}){ext}"


# --------------------------------------------------------------------------- #
# 视频下载（yt-dlp）
# --------------------------------------------------------------------------- #
def find_ffmpeg():
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    return None


def find_deno():
    """查找 deno（YouTube 解析需要），找到后加入 PATH。"""
    import shutil
    if shutil.which("deno"):
        return True
    # 候选位置：程序目录、PyInstaller 临时目录
    candidates = [app_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(meipass)
    for d in candidates:
        exe = os.path.join(d, "deno.exe")
        if os.path.isfile(exe):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            return True
    # WinGet 安装路径（找到就停，别整棵树扫完）
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    if os.path.isdir(base):
        try:
            for root, dirs, files in os.walk(base):
                if "deno.exe" in files:
                    os.environ["PATH"] = root + os.pathsep + os.environ.get("PATH", "")
                    return True
        except OSError:
            pass
    return False


def _looks_like_stream_file(path):
    """判断是不是 yt-dlp 没合并的中间文件（xxx.f299.mp4 / xxx.f140.m4a）。

    正常成品是「标题.mp4」，不会带 .f<数字> 这种流编号。
    """
    name = os.path.basename(path or "")
    return re.search(r"\.f\d+\.[A-Za-z0-9]+$", name) is not None


class _CaptureLogger:
    """把 yt-dlp 的错误记下来。

    开了 ignoreerrors 之后，yt-dlp 不再抛异常，只通过 logger.error() 报告；
    不接管它，用户就只能看到我们写的笼统提示，看不到真实原因。
    """

    def __init__(self, sink):
        self.sink = sink

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        text = str(msg or "").strip()
        if text:
            self.sink.append(text)


def _build_ydl_format(audio_only, quality, compat=True):
    """拼 yt-dlp 的格式串。

    compat=True（默认）：优先 H.264 + AAC，手机能直接放。
    compat=False：要最高画质，YouTube 的 4K/8K 只有 AV1/VP9 + Opus，
    很多手机播放器放不出画面、iPhone 上还没有声音。
    """
    if audio_only:
        return "bestaudio/best"
    height = QUALITY_HEIGHTS.get(quality)
    if compat:
        if height:
            return (f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                    f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best")
        return ("bestvideo[vcodec^=avc1]+bestaudio[ext=m4a]/"
                "best[ext=mp4]/bestvideo+bestaudio/best")
    if height:
        return (f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best")
    return "bestvideo+bestaudio/best"


# yt-dlp 的抖音解析器只认 www.douyin.com/video/<数字>，短链和分享页要先还原
DOUYIN_ID_RE = re.compile(r"(?:/video/|modal_id=)(\d{6,})")


def resolve_video_url(url):
    """把抖音短链/分享页换成解析器认的标准形式。其它链接原样返回，失败也原样返回。"""
    low = (url or "").lower()
    if "douyin.com" not in low and "iesdouyin.com" not in low:
        return url

    # 分享页/标准页里本来就带 id，直接拼
    if "v.douyin.com" not in low:
        m = DOUYIN_ID_RE.search(url)
        if m:
            return f"https://www.douyin.com/video/{m.group(1)}"
        return url

    # 短链：跟一次 302 拿到真实地址
    if not HAS_REQUESTS:
        return url
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                            allow_redirects=True, timeout=15, stream=True)
        final = resp.url or ""
        resp.close()
        m = re.search(r"/video/(\d+)", final)
        if m:
            return f"https://www.douyin.com/video/{m.group(1)}"
        return final if "douyin.com/video/" in final else url
    except Exception:
        return url


# 把第三方库的英文报错换成看得懂的处理建议
FRIENDLY_ERRORS = (
    ("fresh cookies",
     "抖音要求真实浏览器的登录态。办法：完全退出 Chrome（任务管理器里确认没有残留）→ 程序里「或读浏览器」选 Chrome；"
     "或者用浏览器插件导出 Netscape 格式的 cookies 文件，填进 cookies 那一栏"),
    ("could not copy chrome cookie database",
     "Chrome 正在运行，yt-dlp 拷不了它的 cookie 数据库。先完全退出 Chrome 再试，或改用导出的 cookies 文件"),
    ("could not copy edge cookie database",
     "Edge 正在运行，yt-dlp 拷不了它的 cookie 数据库。先完全退出 Edge 再试，或改用导出的 cookies 文件"),
    ("could not copy firefox cookie database",
     "Firefox 正在运行，先完全退出 Firefox 再试"),
    ("failed to decrypt with dpapi",
     "Chrome / Edge 从 127 版起给 cookie 加了系统级加密（App-Bound Encryption），yt-dlp 解不开。"
     "改用 Firefox（cookie 不加密，选中即可读），或者手工导出一份 cookies.txt 填进左边那栏"),
    ("sign in to confirm",
     "站点要求人机验证（YouTube 常见，随机触发，跟节点和视频都有关）。"
     "先多试两次，常能过；要稳定就给它一份「已登录」的 cookies 文件——"
     "在 Firefox 里登录后选 Firefox，或用插件从已登录的浏览器导出 cookies.txt。"
     "（Chrome 的 cookies 新版加了系统级加密，程序读不了）"),
    ("unexpected_eof",
     "连接被中途切断——常见于代理节点不稳或被拦截。换一个代理节点，或者等一会儿重试。"
     "（不是链接的问题，也不是程序的问题）"),
    ("unable to download api page",
     "连不上站点的接口，多半是代理/网络问题。换节点或关掉代理直连再试"),
)


def friendly_error(msg):
    low = str(msg).lower()
    for key, hint in FRIENDLY_ERRORS:
        if key in low:
            return hint
    return str(msg)


def failure_hint(url, exc):
    """在通用中文提示后面，按站点补一句更具体的实话。"""
    hint = friendly_error(exc)
    low = str(exc).lower()
    if "douyin.com" in (url or "").lower() and "403" in low:
        hint += ("\n    国内抖音 2026-10 起风控收紧：实测 yt-dlp（最新版）和 f2（最新版）"
                 "带上已登录的 cookies 也一律 403，目前没有可用方案。"
                 "急用请直接用抖音客户端保存，不必在程序里反复试。")
    return hint


def download_video(url, outdir, audio_only, quality, cookies, browser, emit, cancel=None, retries=3,
                   compat=True):
    real_url = resolve_video_url(url)
    if real_url != url:
        emit("log", f"  链接已还原：{real_url}")
    url = real_url

    os.makedirs(outdir, exist_ok=True)
    state_errors = []          # yt-dlp 的真实错误（ignoreerrors 会吞掉它们）
    opts = {
        # 相册（Instagram carousel、播放列表）里每项的标题常常一样，
        # 不加序号会互相覆盖——2026-10-26 实测：12 个视频只剩 1 个。
        # playlist_index 只在多条目时才有值，单个视频渲染成空。
        "outtmpl": os.path.join(outdir, "%(extractor_key)s",
                                "%(title)s%(playlist_index& [{:02d}]|)s.%(ext)s"),
        "format": _build_ydl_format(audio_only, quality, compat),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "retries": retries,
        "fragment_retries": retries,
        "socket_timeout": 30,
        "concurrent_fragment_downloads": 8,
        "quiet": True,
        "no_warnings": True,
        # 相册/图集帖里常混着图片子项，yt-dlp 遇到会报 "No video formats found"。
        # 不开这个，一个图片子项会让整条帖子放弃（Instagram 的 12 个视频就是这样丢的）。
        # 代价：真实错误也被吞掉，所以下面挂一个 logger 把错误捞出来，
        # 否则用户只会看到笼统的「没有可下载的内容」（2026-10-28 踩过）。
        "ignoreerrors": True,
        "logger": _CaptureLogger(state_errors),
        "postprocessors": [],
    }
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    if audio_only:
        opts["postprocessors"].append({"key": "FFmpegExtractAudio",
                                       "preferredcodec": "mp3", "preferredquality": "192"})
    if browser:
        opts["cookiesfrombrowser"] = (browser, None, None, None)
    elif cookies:
        opts["cookiefile"] = cookies

    state = {}

    def hook(d):
        # 在下载过程中检查取消：yt-dlp 会把这个异常往上抛，外层接住当取消处理
        if cancel is not None and cancel.is_set():
            raise Cancelled("已取消")
        if d.get("status") == "downloading":
            emit("progress", d)
        elif d.get("status") == "finished":
            state["path"] = d.get("filename") or d.get("filepath")
            emit("log", "  正在合并/转码……")

    opts["progress_hooks"] = [hook]
    last = None
    for attempt in range(1, retries + 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled("已取消")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            if info is None:
                # ignoreerrors=True 时，整体失败会返回 None 而不是抛异常。
                # 真实原因在 logger 里，别拿笼统的话糊弄用户。
                real = _brief(state_errors[-1]) if state_errors else ""
                if real:
                    raise DownloadFailed(f"下载失败：{real}")
                raise DownloadFailed("这条链接没有可下载的内容（可能是纯图片帖，或需要登录）")
            # 优先用 yt-dlp 给的最终文件路径：合并后是 .mp4，
            # 而 progress hook 最后一条记的是音频流（.m4a），会误导日志。
            path = None
            for item in (info.get("requested_downloads") or []):
                if item.get("filepath"):
                    path = item["filepath"]
                    break
            if not path:
                path = info.get("filepath") or info.get("_filename") or state.get("path")
            if not path:
                raise DownloadFailed("下载完成但未能定位输出文件")
            # yt-dlp 的中间文件名长这样：xxx.f299.mp4（视频流）、xxx.f140.m4a（音频流）。
            # 出现它说明「没合并」——通常是音频下载中断，被 ignoreerrors 吞掉了，
            # 结果只剩一条视频轨，看着像成品其实没声音。2026-10-28 实测踩过。
            if _looks_like_stream_file(path):
                raise DownloadFailed(
                    "只下到了视频流或音频流（合并没发生），这条要重下。\n"
                    "    常见原因：下载音频时网络断了。文件是完整的视频但没有声音。")
            return path
        except Cancelled:
            raise
        except YTDL_DownloadError as e:
            if cancel is not None and cancel.is_set():
                raise Cancelled("已取消") from e
            msg = str(e).lower()
            if any(k in msg for k in ("unsupported url", "no video formats", "video unavailable",
                                      "private video", "not a valid url")):
                raise UnsupportedUrl(str(e).replace("ERROR: ", "").splitlines()[0]) from e
            if any(k in msg for k in ("fresh cookies", "cookie database", "sign in to confirm")):
                # 缺登录态重试没用，直接失败，省掉后面两轮白等
                raise DownloadFailed(str(e).replace("ERROR: ", "").splitlines()[0]) from e
            last = e
        except Exception as e:
            last = e
        if attempt < retries:
            emit("log", f"  重试 {attempt}/{retries} ……")
            time.sleep(2 * attempt)
    # 有 logger 抓到的真实错误就报它，比「重试 3 次后仍失败」有用
    if state_errors:
        raise DownloadFailed(f"重试 {retries} 次后仍失败：{_brief(state_errors[-1])}")
    raise DownloadFailed(f"重试 {retries} 次后仍失败：{_brief(last)}")


# --------------------------------------------------------------------------- #
# 图片下载
# --------------------------------------------------------------------------- #
def _build_headers(url):
    h = {"User-Agent": USER_AGENT}
    host = url.split("/", 3)[2].lower() if "://" in url else ""
    if any(k in host for k in ("sinaimg.cn", "sina.com.cn", "weibo.com")):
        h["Referer"] = "https://weibo.com/"
    return h


def _upgrade_sinaimg_url(url):
    """微博 CDN：把小图尺寸段自动替换成大图（large）。"""
    if "sinaimg.cn" not in url:
        return url
    for seg in ("/orj360/", "/small/", "/bmiddle/", "/mw690/", "/thumb150/", "/thumb180/"):
        if seg in url:
            return url.replace(seg, "/large/", 1)
    return url


def _download_direct(url, outdir, emit, cancel=None):
    """下载单张直链图片，返回文件路径。先写 .part，成功才改名。"""
    if not HAS_REQUESTS:
        raise DownloadFailed("缺少 requests 依赖，请通过启动器重新安装")
    url = _upgrade_sinaimg_url(url)
    emit("progress", {"total": 0, "downloaded": 0, "speed": None, "eta": None})
    headers = _build_headers(url)
    with requests.get(url, stream=True, timeout=30, headers=headers) as resp:
        try:
            resp.raise_for_status()
        except Exception as e:
            raise DownloadFailed(f"请求失败（HTTP 状态 {resp.status_code}）") from e
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if ctype and not ctype.startswith("image/") and not is_direct_image_url(url):
            raise UnsupportedUrl(f"链接返回的不是图片（Content-Type：{ctype or '未知'}）")

        total = int(resp.headers.get("Content-Length") or 0)
        filename = _derive_filename(resp, url, ctype)
        path = _unique_path(os.path.join(outdir, filename))
        tmp = path + ".part"
        downloaded = 0
        try:
            with open(tmp, "wb") as f:
                for chunk in resp.iter_content(65536):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("已取消")
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    emit("progress", {"total": total, "downloaded": downloaded,
                                      "speed": None, "eta": None})
            os.replace(tmp, path)
        except BaseException:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
    return path


def _download_gallery(url, options, emit, cancel=None, retries=3):
    """用 gallery-dl 下载一个图站链接。成功返回 0，失败抛异常。

    注意：DownloadJob.run() 返回的是整数状态码（位掩码），不是对象。
    """
    last_rc = 0
    last_err = None
    for attempt in range(1, retries + 1):
        if cancel is not None and cancel.is_set():
            raise Cancelled("已取消")
        try:
            rc = int(gd_job.DownloadJob(url).run() or 0)
        except Cancelled:
            raise
        except Exception as e:
            kind = _classify(e)
            if kind == "not_found":
                raise UnsupportedUrl(_brief(e)) from e
            if kind == "no_extractor":
                raise  # 交给上层尝试直链下载
            last_err = e
            if attempt < retries:
                emit("log", f"  下载失败（{_brief(e)}），重试 {attempt}/{retries} ……")
                time.sleep(2 * attempt)
            continue

        if rc == 0:
            return 0
        last_rc = rc
        # 只有网络/磁盘错误值得重试；部分文件失败重试会重复下载
        if (rc & 128) and attempt < retries:
            emit("log", f"  {_rc_message(rc)}，重试 {attempt}/{retries} ……")
            time.sleep(2 * attempt)
            continue
        break

    if last_rc:
        raise DownloadFailed(_rc_message(last_rc))
    raise DownloadFailed(f"重试 {retries} 次后仍失败：{_brief(last_err)}")


def download_one(url, outdir, options, emit, cancel=None, retries=3):
    """下载单个链接（直链或图站），返回文件路径（直链）或 None（图站）。"""
    if is_direct_image_url(url):
        emit("log", "  识别为直接图片链接，开始下载……")
        return _download_direct(url, outdir, emit, cancel=cancel)

    emit("progress", {"total": 0, "downloaded": 0, "speed": None, "eta": None})
    emit("log", "  识别为图站/相册链接，正在解析图片列表……")
    try:
        _download_gallery(url, options, emit, cancel=cancel, retries=retries)
        emit("log", "  ✓ 图站下载完成（状态码 0）")
        return None
    except (Cancelled, UnsupportedUrl, DownloadFailed):
        raise
    except Exception as e:
        if _classify(e) == "no_extractor":
            # gallery-dl 不认识该链接，尝试当作直链下载
            emit("log", "  未匹配到图站规则，尝试作为直接图片下载……")
            return _download_direct(url, outdir, emit, cancel=cancel)
        raise


def build_options(outdir, skip_duplicates, cookies, browser=None):
    gd_config.load()
    gd_config.set((), "base-directory", outdir)
    gd_config.set((), "output", {"mode": "null"})
    gd_config.set((), "skip", bool(skip_duplicates))
    if browser:
        # gallery-dl 的 cookies 允许 (浏览器, profile, keyring, container, domain) 元组
        gd_config.set((), "cookies", (browser, None, None, None, None))
    elif cookies:
        gd_config.set((), "cookies", cookies)


# --------------------------------------------------------------------------- #
# 日志捕获（把 gallery-dl 的日志转发到界面）
# --------------------------------------------------------------------------- #
class _QueueLogHandler(logging.Handler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        try:
            msg = self.format(record)
            if msg and msg.strip():
                self.callback(msg.strip())
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 图形界面
# --------------------------------------------------------------------------- #
class App:
    CONFIG_PATH = os.path.join(app_dir(), "config.json")
    HISTORY_PATH = os.path.join(app_dir(), "history.json")
    LOG_DIR = os.path.join(app_dir(), "logs")

    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.working = False
        self.cancel_event = threading.Event()
        self._concurrent = False   # 并发数 > 1 时进度条改为不确定模式
        self._cur_label = ""       # 正在处理第几条，显示在状态栏
        self.last_failed = []      # [(url, 原因)]，给「重试失败」用
        self._update_info = None   # 更新清单，None = 还没拿到
        self._update_tries = 0
        self._update_silent = True
        self.config = self._load_config()
        self.history = self._load_history()
        self._prune_logs()
        self._log_handler = self._make_log_handler()
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------- 配置 / 历史 ----------
    def _load_history(self):
        try:
            with open(self.HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return set(data)
        except Exception:
            pass
        return set()

    def _save_history(self):
        try:
            with open(self.HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(sorted(self.history), f, ensure_ascii=False)
        except Exception:
            pass

    def _log_file(self):
        return os.path.join(self.LOG_DIR, time.strftime("%Y-%m-%d") + ".log")

    def _log_to_file(self, line):
        try:
            os.makedirs(self.LOG_DIR, exist_ok=True)
            with open(self._log_file(), "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + str(line) + "\n")
        except OSError:
            pass

    def _prune_logs(self, keep_days=14):
        """只留最近 14 天的日志，免得越攒越多。"""
        try:
            cutoff = time.time() - keep_days * 86400
            for name in os.listdir(self.LOG_DIR):
                if not name.lower().endswith(".log"):
                    continue
                p = os.path.join(self.LOG_DIR, name)
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
        except OSError:
            pass

    def _load_config(self):
        try:
            with open(self.CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                return cfg
        except Exception:
            pass
        return {}

    def _save_config(self):
        try:
            cfg = {
                "outdir": self.outdir_var.get(),
                "cookies": self.cookies_var.get(),
                "browser": self.browser_var.get(),
                "skip": bool(self.skip_var.get()),
                "proxy_port": self.proxy_var.get().strip(),
                "mode": self.mode_var.get(),
                "quality": self.quality_var.get(),
                "format": self.format_var.get(),
                "beep": bool(self.beep_var.get()),
                "compat": bool(self.compat_var.get()),
                "concurrency": self.concurrency_var.get(),
            }
            with open(self.CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _on_close(self):
        if self.working:
            if not messagebox.askyesno("还在下载", "下载还没结束，确定要退出吗？"):
                return
            self.cancel_event.set()
        self._save_config()
        self.root.destroy()

    def _make_log_handler(self):
        handler = _QueueLogHandler(self._queue_log)
        handler.setFormatter(logging.Formatter("%(message)s"))
        for name in ("gallery-dl", "gallery_dl"):
            lg = logging.getLogger(name)
            lg.setLevel(logging.INFO)
            lg.propagate = False
            lg.addHandler(handler)
        return handler

    def _queue_log(self, msg):
        self.q.put(("log", "  " + msg))

    # ---------- 界面 ----------
    def build_ui(self):
        self.root.title(f"MediaGrab v{APP_VERSION}  ·  图片 / 视频")
        self.root.geometry("860x660")
        self.root.minsize(760, 600)

        frm = ttk.Frame(self.root, padding=10)
        frm.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)
        frm.columnconfigure(2, weight=0)

        pad = {"padx": 6, "pady": 4}

        ttk.Label(frm, text="下载链接（每行一个，图站/直链/视频站均可）：").grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)

        self.url_text = tk.Text(frm, height=5, wrap="word", undo=True)
        self.url_text.grid(row=1, column=0, columnspan=2, sticky="ew", **pad)
        url_bar = ttk.Scrollbar(frm, orient="vertical", command=self.url_text.yview)
        url_bar.grid(row=1, column=2, sticky="ns")
        self.url_text.config(yscrollcommand=url_bar.set)

        # 下载类型 / 清晰度 / 格式
        type_row = ttk.Frame(frm)
        type_row.grid(row=2, column=0, columnspan=3, sticky="w", **pad)
        ttk.Label(type_row, text="类型：").pack(side="left")
        self.mode_var = tk.StringVar(value=self.config.get("mode", "自动"))
        self.mode_combo = ttk.Combobox(type_row, textvariable=self.mode_var, state="readonly",
                                       width=8, values=["自动", "图片", "视频"])
        self.mode_combo.pack(side="left", padx=(0, 12))
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)

        ttk.Label(type_row, text="清晰度：").pack(side="left")
        self.quality_var = tk.StringVar(value=self.config.get("quality", "最佳"))
        self.quality_combo = ttk.Combobox(type_row, textvariable=self.quality_var, state="readonly",
                                           width=8, values=["最佳", "1080p", "720p", "480p"])
        self.quality_combo.pack(side="left", padx=(0, 12))

        ttk.Label(type_row, text="格式：").pack(side="left")
        self.format_var = tk.StringVar(value=self.config.get("format", "MP4 视频"))
        self.format_combo = ttk.Combobox(type_row, textvariable=self.format_var, state="readonly",
                                         width=10, values=["MP4 视频", "MP3 音频"])
        self.format_combo.pack(side="left", padx=(0, 12))

        # 保存目录
        ttk.Label(frm, text="保存到：").grid(row=3, column=0, sticky="w", **pad)
        default_dir = self.config.get("outdir") or os.path.join(app_dir(), "downloads")
        self.outdir_var = tk.StringVar(value=default_dir)
        ttk.Entry(frm, textvariable=self.outdir_var).grid(row=3, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="浏览…", command=self.on_browse).grid(row=3, column=2, sticky="w", **pad)

        # 选项
        opts = ttk.Frame(frm)
        opts.grid(row=4, column=0, columnspan=3, sticky="w", **pad)
        self.skip_var = tk.BooleanVar(value=self.config.get("skip", True))
        ttk.Checkbutton(opts, text="跳过已存在的文件", variable=self.skip_var).pack(side="left", padx=(0, 16))
        self.beep_var = tk.BooleanVar(value=self.config.get("beep", True))
        ttk.Checkbutton(opts, text="完成提示音", variable=self.beep_var).pack(side="left", padx=(0, 16))
        # 默认勾上：YouTube 的 4K/8K 是 AV1/VP9 + Opus，手机常常放不出来
        self.compat_var = tk.BooleanVar(value=self.config.get("compat", True))
        ttk.Checkbutton(opts, text="手机兼容（H.264）", variable=self.compat_var).pack(side="left", padx=(0, 16))
        ttk.Label(opts, text="同时下载量：").pack(side="left")
        self.concurrency_var = tk.StringVar(value=str(self.config.get("concurrency", "1")))
        ttk.Combobox(opts, textvariable=self.concurrency_var, state="readonly", width=4,
                     values=["1", "2", "3"]).pack(side="left", padx=(0, 16))

        # Cookies（可选）：给文件，或者直接从浏览器读
        ttk.Label(frm, text="Cookies（登录用，二选一）：").grid(row=5, column=0, sticky="w", **pad)
        ck = ttk.Frame(frm)
        ck.grid(row=5, column=1, columnspan=2, sticky="ew", **pad)
        ck.columnconfigure(0, weight=1)
        self.cookies_var = tk.StringVar(value=self.config.get("cookies", ""))
        ttk.Entry(ck, textvariable=self.cookies_var).grid(row=0, column=0, sticky="ew")
        ttk.Label(ck, text="或读浏览器").grid(row=0, column=1, padx=(8, 4))
        self.browser_var = tk.StringVar(value=self.config.get("browser", "不用"))
        ttk.Combobox(ck, textvariable=self.browser_var, state="readonly", width=9,
                     values=BROWSER_CHOICES).grid(row=0, column=2)
        ttk.Button(ck, text="浏览…", command=self.on_browse_cookies).grid(row=0, column=3, padx=(6, 0))

        # 代理端口
        ttk.Label(frm, text="代理端口（0=直连）：").grid(row=6, column=0, sticky="w", **pad)
        default_port = str(self.config.get("proxy_port", "7897"))
        self.proxy_var = tk.StringVar(value=default_port)
        ttk.Entry(frm, textvariable=self.proxy_var, width=10).grid(row=6, column=1, sticky="w", **pad)

        # 按钮
        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, columnspan=3, sticky="w", **pad)
        self.download_btn = ttk.Button(btns, text="开始下载", command=self.on_download)
        self.download_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ttk.Button(btns, text="停止", command=self.on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 12))
        self.retry_btn = ttk.Button(btns, text="重试失败", command=self.on_retry_failed, state="disabled")
        self.retry_btn.pack(side="left", padx=(0, 12))
        ttk.Button(btns, text="清空链接", command=self.on_clear).pack(side="left", padx=6)
        ttk.Button(btns, text="导入txt…", command=self.on_import_txt).pack(side="left", padx=6)
        ttk.Button(btns, text="清空历史", command=self.on_clear_history).pack(side="left", padx=6)
        ttk.Button(btns, text="查看历史", command=self.on_view_history).pack(side="left", padx=6)
        ttk.Button(btns, text="打开保存目录", command=self.on_open_dir).pack(side="left", padx=6)

        # 进度条 + 状态
        self.progress = ttk.Progressbar(frm, mode="determinate", maximum=100)
        self.progress.grid(row=8, column=0, columnspan=3, sticky="ew", **pad)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(frm, textvariable=self.status_var, anchor="w").grid(
            row=9, column=0, columnspan=3, sticky="ew", **pad)

        # 日志
        self.log = scrolledtext.ScrolledText(frm, height=10, state="disabled", wrap="word")
        self.log.grid(row=10, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(10, weight=1)

        # 底部：日志落盘位置 + 检查更新
        foot = ttk.Frame(frm)
        foot.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(0, 2))
        self.log_hint_var = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.log_hint_var, foreground="#777").pack(side="left", padx=6)
        ttk.Button(foot, text="打开日志", command=self.on_open_log).pack(side="right", padx=6)
        self.update_btn = ttk.Button(foot, text="检查更新", command=self.on_check_update)
        self.update_btn.pack(side="right", padx=(0, 6))

        self._append_log(f"MediaGrab v{APP_VERSION}")
        self._append_log("粘贴图站/直链/视频链接，每行一个。自动模式会根据链接选择下载方式。")
        deps = []
        deps.append("gallery-dl ✓" if (HAS_GALLERYDL and HAS_REQUESTS) else "gallery-dl ✗")
        deps.append("yt-dlp ✓" if HAS_YTDLP else "yt-dlp ✗")
        self._append_log("依赖：" + "   ".join(deps))
        log_name = os.path.basename(self._log_file())
        self.log_hint_var.set(f"日志：logs\\{log_name}")
        self._append_log(f"日志文件：logs\\{log_name}")
        self._on_mode_change()
        self._start_update_check(silent=True)

    def _on_mode_change(self, event=None):
        is_video = (self.mode_var.get() == "视频")
        self.quality_combo.configure(state="readonly" if is_video else "disabled")
        self.format_combo.configure(state="readonly" if is_video else "disabled")

    # ---------- 交互 ----------
    def _append_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", str(text) + "\n")
        self.log.see("end")
        self.log.config(state="disabled")
        self._log_to_file(text)

    def on_browse(self):
        d = filedialog.askdirectory(title="选择保存目录", initialdir=self.outdir_var.get() or app_dir())
        if d:
            self.outdir_var.set(d)

    def on_browse_cookies(self):
        f = filedialog.askopenfilename(title="选择 cookies 文件",
                                       filetypes=[("Netscape cookies", "*.txt"), ("所有文件", "*.*")])
        if f:
            self.cookies_var.set(f)

    def on_clear(self):
        self.url_text.delete("1.0", "end")

    def on_stop(self):
        if not self.working:
            return
        self.cancel_event.set()
        self.stop_btn.config(state="disabled")
        self.status_var.set("正在停止……")
        self._append_log("已请求停止：当前这一条做完就停（图站链接无法中途打断）。")

    def _update_retry_btn(self):
        n = len(self.last_failed)
        self.retry_btn.config(text=f"重试失败 ({n})" if n else "重试失败",
                              state="normal" if (n and not self.working) else "disabled")

    def on_retry_failed(self):
        """把上一轮失败的链接填回输入框，再跑一遍。"""
        if self.working or not self.last_failed:
            return
        if not self._confirm_retry():
            return
        urls = [u for u, _ in self.last_failed]
        self.last_failed = []
        self._update_retry_btn()
        self.url_text.delete("1.0", "end")
        self.url_text.insert("1.0", "\n".join(urls))
        self._append_log(f"重试上一轮失败的 {len(urls)} 条链接……")
        self.on_download()

    def _confirm_retry(self):
        """列出失败清单让用户看一眼，确认了才重试。"""
        win = tk.Toplevel(self.root)
        win.title(f"确认重试 {len(self.last_failed)} 条")
        win.geometry("680x380")
        win.transient(self.root)
        txt = scrolledtext.ScrolledText(win, wrap="word")
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        for i, (u, why) in enumerate(self.last_failed, 1):
            txt.insert("end", f"{i}. {u}\n   原因：{why}\n\n")
        txt.config(state="disabled")
        answer = {"ok": False}

        def yes():
            answer["ok"] = True
            win.destroy()

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="取消", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="重试这些", command=yes).pack(side="right", padx=(0, 6))
        win.grab_set()
        self.root.wait_window(win)
        return answer["ok"]

    def on_clear_history(self):
        if not self.history:
            messagebox.showinfo("提示", "历史记录已经是空的。")
            return
        if messagebox.askyesno("确认", f"清空 {len(self.history)} 条历史记录？"):
            self.history.clear()
            self._save_history()
            messagebox.showinfo("完成", "历史记录已清空。")

    def on_view_history(self):
        win = tk.Toplevel(self.root)
        win.title(f"下载历史（共 {len(self.history)} 条）")
        win.geometry("700x500")
        txt = scrolledtext.ScrolledText(win, wrap="word")
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        if self.history:
            for u in sorted(self.history):
                txt.insert("end", u + "\n")
        else:
            txt.insert("end", "（暂无历史记录）")
        txt.config(state="disabled")

    def on_import_txt(self):
        f = filedialog.askopenfilename(title="选择链接列表 txt",
                                       filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not f:
            return
        try:
            with open(f, "r", encoding="utf-8") as fp:
                lines = [ln.strip() for ln in fp if ln.strip() and not ln.strip().startswith("#")]
        except UnicodeDecodeError:
            with open(f, "r", encoding="gbk") as fp:
                lines = [ln.strip() for ln in fp if ln.strip() and not ln.strip().startswith("#")]
        if not lines:
            messagebox.showinfo("提示", "文件里没有找到有效链接。")
            return
        existing = self.url_text.get("1.0", "end").strip()
        if existing:
            self.url_text.insert("end", "\n" + "\n".join(lines))
        else:
            self.url_text.insert("1.0", "\n".join(lines))
        self._append_log(f"已从 {os.path.basename(f)} 导入 {len(lines)} 个链接。")

    def on_open_dir(self):
        d = self.outdir_var.get().strip() or os.path.join(app_dir(), "downloads")
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)
        except Exception as e:
            messagebox.showerror("错误", f"无法打开目录：{e}")

    def on_open_log(self):
        try:
            os.makedirs(self.LOG_DIR, exist_ok=True)
            os.startfile(self.LOG_DIR)
        except Exception as e:
            messagebox.showerror("错误", f"无法打开日志目录：{e}")

    # ---------- 检查更新 ----------
    def _start_update_check(self, silent):
        """后台发一次请求，主线程轮询结果——不在子线程里碰 Tk。"""
        self._update_silent = silent
        self._update_info = None
        self._update_tries = 0
        threading.Thread(target=self._bg_fetch_update, daemon=True).start()
        self.root.after(600, self._poll_update)

    def _bg_fetch_update(self):
        self._update_info = fetch_update_info()

    def _poll_update(self):
        if self._update_info is not None or self._update_tries >= 12:
            self._apply_update()
            return
        self._update_tries += 1
        self.root.after(500, self._poll_update)

    def _apply_update(self):
        info = self._update_info
        if not info:
            if not self._update_silent:
                self._append_log("检查更新：拿不到更新清单（没配地址，或网络不通）")
            return
        if is_newer(info["version"]):
            self.update_btn.config(text=f"新版本 {info['version']}")
            self._append_log(f"发现新版本 {info['version']}（当前 {APP_VERSION}）。点击「新版本」看说明。")
        elif not self._update_silent:
            self._append_log(f"检查更新：已是最新（{APP_VERSION}）")

    def on_check_update(self):
        info = self._update_info
        if info and is_newer(info["version"]):
            self._offer_download(info)
            return
        self._append_log("正在检查更新……")
        self._start_update_check(silent=False)

    def _offer_download(self, info):
        note = info.get("note") or "（这一版没写说明）"
        if messagebox.askyesno("发现新版本",
                               f"当前 {APP_VERSION}，最新 {info['version']}。\n\n{note}\n\n"
                               "打开下载页面吗？（装新版会覆盖旧版，配置和历史保留）"):
            if info.get("url"):
                webbrowser.open(info["url"])
            else:
                self._append_log("更新清单里没给下载地址（url 字段是空的）")

    def _collect_urls(self):
        text = self.url_text.get("1.0", "end")
        urls = []
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
        return list(dict.fromkeys(urls))

    def on_download(self):
        if self.working:
            return
        mode = self.mode_var.get()
        if mode in ("自动", "图片") and (not HAS_GALLERYDL or not HAS_REQUESTS):
            messagebox.showerror("缺少依赖", "未检测到 gallery-dl / requests，请先运行：pip install gallery-dl requests")
            return
        if mode in ("自动", "视频") and not HAS_YTDLP:
            messagebox.showerror("缺少依赖", "未检测到 yt-dlp，请先运行：pip install yt-dlp imageio-ffmpeg")
            return
        urls = self._collect_urls()
        if not urls:
            messagebox.showwarning("提示", "请先在输入框粘贴至少一个链接。")
            return

        # 历史里去过的链接：只提醒，不再硬拦
        dupes = [u for u in urls if clean_url(u) in self.history]
        if dupes:
            if not messagebox.askyesno("重复链接",
                                        f"有 {len(dupes)} 个链接之前下过了，还继续吗？\n"
                                        "（已存在的文件由「跳过已存在的文件」控制，直链会另存为 (1)）"):
                return

        # 所有界面值在这里（主线程）一次性快照，后台线程不再碰 Tk
        s = {
            "proxy_port": self.proxy_var.get().strip(),
            "outdir": self.outdir_var.get().strip(),
            "cookies": self.cookies_var.get().strip() or None,
            "browser": BROWSER_KEYS.get(self.browser_var.get(), ""),
            "mode": mode,
            "quality": "best" if self.quality_var.get() == "最佳" else self.quality_var.get(),
            "audio_only": (self.format_var.get() == "MP3 音频"),
            "skip": bool(self.skip_var.get()),
            "compat": bool(self.compat_var.get()),
            "concurrency": self._safe_int(self.concurrency_var.get(), 1),
        }

        self.cancel_event.clear()
        self.working = True
        self._concurrent = s["concurrency"] > 1
        self._cur_label = ""
        self.last_failed = []
        self._update_retry_btn()
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.progress["value"] = 0
        self.progress["mode"] = "determinate"
        self.status_var.set("准备下载……")
        self._append_log("=" * 40)
        self._append_log(f"开始下载，共 {len(urls)} 个链接（模式：{mode}）。")

        threading.Thread(target=self.worker, args=(urls, s, self.cancel_event), daemon=True).start()
        self.root.after(100, self.poll)

    @staticmethod
    def _safe_int(value, default):
        try:
            return int(str(value).strip())
        except Exception:
            return default

    # ---------- 后台下载 ----------
    def worker(self, urls, s, cancel):
        def emit(tag, data=None):
            self.q.put((tag, data))

        ok = [0]
        fail = [0]
        cancelled = [0]
        new_urls = []
        failed = []
        lock = threading.Lock()

        try:
            # 设置代理（先检测端口是否通，不通则直连）
            port = s["proxy_port"]
            use_proxy = False
            if port and port != "0":
                try:
                    conn = socket.create_connection(("127.0.0.1", int(port)), timeout=1)
                    conn.close()
                    use_proxy = True
                except Exception:
                    use_proxy = False
            if use_proxy:
                os.environ["HTTP_PROXY"] = f"http://127.0.0.1:{port}"
                os.environ["HTTPS_PROXY"] = f"http://127.0.0.1:{port}"
                emit("log", f"代理已开启（127.0.0.1:{port}）")
            else:
                os.environ.pop("HTTP_PROXY", None)
                os.environ.pop("HTTPS_PROXY", None)
                if port and port != "0":
                    emit("log", f"代理端口 {port} 未响应，使用直连")

            outdir = s["outdir"] or os.path.join(app_dir(), "downloads")
            os.makedirs(outdir, exist_ok=True)

            cookies = s["cookies"]
            browser = s.get("browser") or ""
            mode = s["mode"]
            quality = s["quality"]
            audio_only = s["audio_only"]
            # 图片通道是否可用，看依赖在不在；build_options 只写 gallery-dl 的全局配置，
            # 没有返回值，不能拿它的返回值当"可用"标志（v2.3.2 之前就是这么错的：
            # 所有图站和直链图片都被误判成"缺少依赖"而失败）。
            img_ready = bool(HAS_GALLERYDL and HAS_REQUESTS)
            if img_ready and mode in ("自动", "图片"):
                build_options(outdir, s["skip"], cookies, browser)

            emit("log", f"输出目录：{outdir}")
            emit("log", f"跳过已存在文件：{'是' if s['skip'] else '否'}")
            if browser:
                emit("log", f"登录态：直接读 {browser} 的 cookies")
            elif cookies:
                emit("log", f"登录态：cookies 文件 {os.path.basename(cookies)}")

            total = len(urls)
            done_count = [0]

            def process_one(idx_url):
                idx, url = idx_url
                with lock:
                    done_count[0] += 1
                    n = done_count[0]

                if cancel.is_set():
                    emit("log", f"[{idx}/{total}] 已取消：{url}")
                    with lock:
                        cancelled[0] += 1
                    return

                emit("progress_label", f"第 {n}/{total} 个")
                emit("log", f"[{idx}/{total}] {url}")
                try:
                    url = validate_url(url)
                except ValueError as e:
                    emit("log", f"  ✗ {e}")
                    with lock:
                        fail[0] += 1
                    return

                use_video = (mode == "视频") or (mode == "自动" and is_video_url(url) and HAS_YTDLP)
                try:
                    if use_video:
                        try:
                            path = download_video(url, outdir, audio_only, quality, cookies,
                                                  browser, emit=emit, cancel=cancel,
                                                  compat=s.get("compat", True))
                        except UnsupportedUrl:
                            # yt-dlp 认不出或说"没有视频格式"（比如纯图片帖），
                            # 换图片通道再试一次——Instagram 的图文帖就靠这条回退。
                            if not img_ready or mode == "视频":
                                raise
                            emit("log", "  视频通道不认识这条，改用图片通道重试……")
                            path = download_one(url, outdir, None, emit=emit, cancel=cancel)
                    else:
                        if not img_ready:
                            raise DownloadFailed("图片下载不可用（缺少 gallery-dl / requests）")
                        path = download_one(url, outdir, None, emit=emit, cancel=cancel)
                    if cancel.is_set():
                        raise Cancelled("已取消")
                    if path:
                        size = os.path.getsize(path) if os.path.isfile(path) else 0
                        emit("log", f"  ✓ 已保存：{path}")
                        emit("log", f"    大小：{human_size(size)}")
                    with lock:
                        ok[0] += 1
                        new_urls.append(url)
                except Cancelled:
                    emit("log", "  ⊘ 已取消")
                    with lock:
                        cancelled[0] += 1
                except UnsupportedUrl as e:
                    msg = friendly_error(e)
                    emit("log", f"  ✗ 无效或不支持的链接：{msg}")
                    with lock:
                        fail[0] += 1
                        failed.append((url, msg))
                except DownloadFailed as e:
                    msg = failure_hint(url, e)
                    emit("log", f"  ✗ 下载失败：{msg}")
                    with lock:
                        fail[0] += 1
                        failed.append((url, msg))
                except Exception as e:
                    msg = friendly_error(e)
                    emit("log", f"  ✗ 意外错误：{msg}")
                    with lock:
                        fail[0] += 1
                        failed.append((url, msg))

            workers = max(1, min(3, s["concurrency"]))
            if workers > 1:
                emit("log", f"并发数：{workers}")
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as ex:
                list(ex.map(process_one, enumerate(urls, 1)))

        except Cancelled:
            emit("log", "已停止。")
        except Exception as e:
            emit("log", f"✗ 任务异常终止：{e}")
            emit("error", f"任务异常终止：{e}")
        finally:
            # 无论成功、失败还是取消，都保证收尾，按钮一定能恢复
            self.q.put(("summary", (ok[0], fail[0], cancelled[0], list(new_urls), list(failed))))
            self.q.put(("done",))

    # ---------- 主线程轮询 ----------
    def _finish_ui(self):
        """恢复成「可以再点开始」的状态。summary 和 done 都会调，重复调无害。"""
        self.working = False
        self.cancel_event.clear()
        self._update_retry_btn()
        self.download_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.progress.stop()
        self.progress["mode"] = "determinate"
        self.progress["value"] = 0
        self.status_var.set("就绪")

    def poll(self):
        try:
            while True:
                item = self.q.get_nowait()
                tag = item[0]
                if tag == "log":
                    self._append_log(item[1])
                elif tag == "progress":
                    self._update_progress(item[1])
                elif tag == "progress_label":
                    self._cur_label = item[1]
                    self.status_var.set(item[1])
                elif tag == "error":
                    self._append_log("✗ " + str(item[1]))
                    messagebox.showerror("错误", str(item[1]))
                elif tag == "summary":
                    ok, fail, cancelled, new_urls, failed = item[1]
                    self.last_failed = list(failed)
                    self._update_retry_btn()
                    self._finish_ui()
                    if new_urls:
                        self.history.update(new_urls)
                        self._save_history()
                    self._append_log("-" * 40)
                    self._append_log(f"汇总：成功 {ok} 个，失败 {fail} 个，取消 {cancelled} 个")
                    if self.beep_var.get():
                        try:
                            import winsound
                            if fail == 0:
                                winsound.MessageBeep(winsound.MB_OK)
                            else:
                                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                        except Exception:
                            pass
                    if fail == 0 and cancelled == 0:
                        messagebox.showinfo("完成", f"全部下载完成！共 {ok} 个链接。")
                    elif cancelled and fail == 0:
                        messagebox.showinfo("已停止", f"已停止：成功 {ok} 个，取消 {cancelled} 个。")
                    else:
                        messagebox.showwarning("完成", f"下载结束：成功 {ok} 个，失败 {fail} 个，取消 {cancelled} 个。\n详见下方日志。")
                elif tag == "done":
                    self._finish_ui()
        except queue.Empty:
            pass

        if self.working:
            self.root.after(100, self.poll)

    def _update_progress(self, d):
        total = d.get("total") or d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded") or d.get("downloaded_bytes") or 0
        speed = d.get("speed")
        parts = []

        if self._concurrent:
            # 并发时几路下载共用一个进度条，百分比会来回跳，没有意义。
            # 索性让进度条来回滚，只把字节数和速度当作"还活着"的证据。
            if self.progress["mode"] != "indeterminate":
                self.progress["mode"] = "indeterminate"
                self.progress.start(12)
            parts.append("正在下载……")
            if total:
                parts.append(f"{human_size(downloaded)} / {human_size(total)}")
        elif total:
            if self.progress["mode"] != "determinate":
                self.progress.stop()
                self.progress["mode"] = "determinate"
            pct = min(100.0, downloaded / total * 100.0)
            self.progress["value"] = pct
            parts.append(f"{pct:.1f}%")
            parts.append(f"{human_size(downloaded)} / {human_size(total)}")
        else:
            if self.progress["mode"] != "indeterminate":
                self.progress["mode"] = "indeterminate"
                self.progress.start(12)
            parts.append("正在下载……")

        if speed:
            parts.append(f"{human_size(speed)}/s")

        text = "  |  ".join(parts)
        self.status_var.set(f"{self._cur_label}  |  {text}" if self._cur_label else text)


def main():
    find_deno()          # 只在启动时找一次，别在 import 时扫盘
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("程序错误", f"发生未预期的错误：\n{e}")
        except Exception:
            pass
