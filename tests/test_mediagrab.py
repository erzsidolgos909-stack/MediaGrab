"""MediaGrab 的纯函数测试。

只测不依赖网络、不依赖 Tk 的部分——这样 CI 和本地都能秒跑完。
真正的下载流程靠人工测（见 维护说明.md 的六条）。
"""

import pytest

import MediaGrab as mg


class TestVideoUrl:
    """「自动」模式靠这张表决定走 yt-dlp 还是 gallery-dl，判错就下错东西。"""

    @pytest.mark.parametrize("url", [
        "https://www.douyin.com/video/7412345678901234567",
        "https://v.douyin.com/iAbCdEf/",
        "https://www.douyin.com/discover?modal_id=7412345678901234567",
        "https://www.kuaishou.com/short-video/3xabc",
        "https://www.tiktok.com/@x/video/123",
        "https://youtu.be/abc",
        "https://www.youtube.com/watch?v=abc",
        "https://www.bilibili.com/video/BV1xx411c7mD",
    ])
    def test_video_sites(self, url):
        assert mg.is_video_url(url), url

    @pytest.mark.parametrize("url", [
        "https://www.instagram.com/p/ABC/",
        "https://www.pixiv.net/artworks/123",
        "https://example.com/cat.jpg",
        "https://x.com/someone/status/123",
        # TikTok 的图片帖：yt-dlp 只认 /video/，/photo/ 必须走 gallery-dl
        "https://www.tiktok.com/@pangjiewrr/photo/7699851645227715858",
        "https://www.tiktok.com/@x/photo/123?is_from_webapp=1&sender_device=pc",
    ])
    def test_image_sites_not_misjudged(self, url):
        assert not mg.is_video_url(url), url


class TestResolveVideoUrl:
    def test_modal_id_becomes_standard_page(self):
        got = mg.resolve_video_url(
            "https://www.douyin.com/discover?modal_id=7412345678901234567")
        assert got == "https://www.douyin.com/video/7412345678901234567"

    def test_iesdouyin_share_becomes_standard_page(self):
        got = mg.resolve_video_url(
            "https://www.iesdouyin.com/share/video/7412345678901234567/")
        assert got == "https://www.douyin.com/video/7412345678901234567"

    def test_other_sites_untouched(self):
        url = "https://www.tiktok.com/@x/video/123"
        assert mg.resolve_video_url(url) == url

    def test_short_link_returns_input_when_network_fails(self, monkeypatch):
        def boom(*args, **kwargs):
            raise OSError("no network")

        monkeypatch.setattr(mg.requests, "get", boom)
        url = "https://v.douyin.com/iAbCdEf/"
        assert mg.resolve_video_url(url) == url


class TestRcMessage:
    def test_single_bits(self):
        assert "部分文件失败" in mg._rc_message(1)
        assert "网络或磁盘" in mg._rc_message(128)
        assert "被中止" in mg._rc_message(4)

    def test_bits_combine(self):
        msg = mg._rc_message(129)
        assert "部分文件失败" in msg and "网络或磁盘" in msg

    def test_unknown_bit(self):
        assert "未知错误" in mg._rc_message(64)


class TestFriendlyError:
    def test_fresh_cookies_gives_chinese_advice(self):
        msg = mg.friendly_error(
            "ERROR: [Douyin] 123: Fresh cookies (not necessarily logged in) are needed")
        assert "抖音" in msg

    def test_dpapi_gives_chinese_advice(self):
        msg = mg.friendly_error(
            "ERROR: Failed to decrypt with DPAPI. See https://github.com/yt-dlp/yt-dlp/issues/10927")
        assert "App-Bound" in msg

    def test_unknown_error_passes_through(self):
        assert mg.friendly_error("ERROR: 404 not found") == "ERROR: 404 not found"

    def test_douyin_403_gets_extra_warning(self):
        msg = mg.failure_hint("https://www.douyin.com/video/1",
                              "HTTP Error 403: Forbidden")
        assert "403" in msg and "抖音" in msg

    def test_douyin_warning_not_leaked_to_other_sites(self):
        msg = mg.failure_hint("https://youtu.be/x", "HTTP Error 403: Forbidden")
        assert "风控收紧" not in msg


class TestHelpers:
    def test_human_size(self):
        assert mg.human_size(0) == "0.00 B"
        assert mg.human_size(1536).endswith("KB")

    def test_clean_url_strips_tracking_params(self):
        got = mg.clean_url("https://a.com/x?utm_source=1&id=2&ref=3")
        assert got == "https://a.com/x?id=2"

    def test_validate_url_requires_http(self):
        assert mg.validate_url("  https://a.com/x  ") == "https://a.com/x"
        with pytest.raises(ValueError):
            mg.validate_url("ftp://a.com/x")

    def test_is_direct_image_url_ignores_query(self):
        assert mg.is_direct_image_url("https://a.com/x.JPG?w=100")
        assert not mg.is_direct_image_url("https://a.com/x.mp4")

    def test_unique_path_adds_suffix(self, tmp_path):
        target = tmp_path / "a.jpg"
        target.write_bytes(b"x")
        got = mg._unique_path(str(target))
        assert got.endswith("a(1).jpg")

    def test_build_ydl_format(self):
        assert mg._build_ydl_format(True, "1080p") == "bestaudio/best"
        assert "height<=720" in mg._build_ydl_format(False, "720p")
        assert mg._build_ydl_format(False, "best") == "bestvideo+bestaudio/best"


class TestDeriveFilename:
    class _Resp:
        def __init__(self, cd=""):
            self.headers = {"Content-Disposition": cd}

    def test_from_content_disposition(self):
        resp = self._Resp('attachment; filename="pic.png"')
        assert mg._derive_filename(resp, "https://a.com/x", "image/png") == "pic.png"

    def test_from_url_when_header_missing(self):
        resp = self._Resp("")
        assert mg._derive_filename(resp, "https://a.com/dir/photo.jpg", "") == "photo.jpg"

    def test_content_type_decides_extension(self):
        resp = self._Resp("")
        got = mg._derive_filename(resp, "https://a.com/image", "image/webp")
        assert got.endswith(".webp")

    def test_illegal_chars_are_stripped(self):
        resp = self._Resp('attachment; filename="a:b*c?.png"')
        got = mg._derive_filename(resp, "https://a.com/x", "image/png")
        assert not any(ch in got for ch in '*?:')

class TestUpdateCheck:
    """检查更新只比对版本号，出错必须静默——不能让它把程序搞崩。"""

    def test_version_tuple(self):
        assert mg.version_tuple("2.3.0") == (2, 3, 0)
        assert mg.version_tuple("v2.3") == (2, 3, 0)
        assert mg.version_tuple("2") == (2, 0, 0)
        assert mg.version_tuple("abc") == ()
        assert mg.version_tuple(None) == ()

    def test_is_newer(self):
        assert mg.is_newer("2.4.0", "2.3.0")
        assert mg.is_newer("2.10.0", "2.9.9")          # 按数字比，不是按字符串
        assert not mg.is_newer("2.3.0", "2.3.0")
        assert not mg.is_newer("2.2.9", "2.3.0")
        assert not mg.is_newer("abc", "2.3.0")          # 解析不了就不报更新

    def test_no_url_means_no_request(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("不该发请求")

        monkeypatch.setattr(mg.requests, "get", boom)
        assert mg.fetch_update_info("") is None

    def test_parses_manifest(self, monkeypatch):
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"version": "9.9.9", "url": "https://x/y.exe", "note": "测试"}

        monkeypatch.setattr(mg.requests, "get", lambda *a, **k: Resp())
        got = mg.fetch_update_info("https://example.com/version.json")
        assert got == {"version": "9.9.9", "url": "https://x/y.exe", "note": "测试"}

    def test_ignores_manifest_without_version(self, monkeypatch):
        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"url": "https://x/y.exe"}

        monkeypatch.setattr(mg.requests, "get", lambda *a, **k: Resp())
        assert mg.fetch_update_info("https://example.com/version.json") is None

    def test_network_error_returns_none(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("no network")

        monkeypatch.setattr(mg.requests, "get", boom)
        assert mg.fetch_update_info("https://example.com/version.json") is None