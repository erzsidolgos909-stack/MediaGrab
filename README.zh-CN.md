# MediaGrab

[English](README.md) | **简体中文**

一个 Windows 上的图片 / 视频下载器：粘链接、选目录、点开始。三类链接自动判断，界面用 Tkinter 写，能打包成单文件 EXE 发给不写代码的人。

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![Tests](https://img.shields.io/badge/tests-44%20passed-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

## 下载

不想装 Python？去 [**发行版**](https://github.com/erzsidolgos909-stack/MediaGrab/releases) 直接拿打包好的：

| 文件 | 是什么 |
|---|---|
| `MediaGrab-x.y.z-setup.exe` | 安装包：有开始菜单、有卸载程序，装到当前用户不弹 UAC |
| `MediaGrab-x.y.z-portable.zip` | 绿色版：解压到任意目录就能跑 |

两个都没有数字签名，Windows 的 SmartScreen 和部分杀毒软件可能拦一下——PyInstaller 打包的通病，加信任即可。

## 能做什么

| 链接类型 | 走哪个后端 | 说明 |
|---|---|---|
| 图站 / 相册 / 作者主页 | gallery-dl | Pixiv、X、Instagram、Reddit、Pinterest、微博、B 站相簿等上千个站点 |
| 直接图片链接 | requests | 单张下载，微博 CDN 的小图自动换成大图 |
| 视频 / 音频 | yt-dlp | YouTube、B 站、TikTok 等；可选 1080p / 720p / 480p，也能只提音频转 MP3 |

其他：

- 多链接一行一个，支持并发（1–3 路）
- 可中断（直链和视频能真打断；图站要等当前这条跑完，gallery-dl 没给中断钩子）
- 失败清单 + 一键重试：失败的链接进清单，点一下重跑，不用翻日志找
- 日志按天落盘，保留 14 天
- cookies 可以读浏览器（Firefox 可读；Chrome / Edge 新版读不了，见「已知限制」）
- 启动时检查更新（只提示，不自动替换自己的 EXE）

## 跑起来

需要 Python 3.10+。

```bash
pip install -r requirements.txt
python MediaGrab.py
```

想要单文件 EXE：

```bash
python -m PyInstaller MediaGrab.spec --noconfirm
```

产物在 `dist\`。注意：程序用 `app_dir()` 定位配置，**EXE 要放在自己单独的目录里**，不然 `config.json` / `history.json` 会跟着 EXE 到处跑。

## 测试

```bash
python -m pytest tests -q
```

44 个用例，约 22 秒：

- **40 个单元测试**（0.4 秒）：只测纯函数——链接判定、状态码翻译、短链还原、报错提示、文件名推导、体积换算
- **4 个端到端测试**：真启动界面、真走线程和队列、本地起 HTTP 服务喂文件，验「贴链接 → 点开始 → 文件落地 → 界面恢复」。不模拟鼠标（那要 pywinauto，对 Tkinter 不稳），但能测出接线对不对

`重新打包.bat` 会先跑全部测试，红了直接拒绝打包。

## 代码结构

单文件约 1400 行，按段切开：

```
MediaGrab.py
├── 依赖探测            缺库不崩，只把标志位设成 False
├── 异常定义            DownloadFailed / UnsupportedUrl / Cancelled
├── 工具函数            URL 清洗、体积换算、文件名推导
├── 更新检查            版本比对 + 清单解析，失败一律静默
├── 视频下载            yt-dlp 封装：重试、取消钩子、登录态
├── 图片下载            gallery-dl 封装 + 直链下载（.part 再改名）
└── App (Tkinter)       界面、线程快照、队列收单
```

三个值得说明的设计：

**一、界面值在主线程快照后再进线程。** Tkinter 不线程安全，所以点「开始下载」时把 8 个控件值读成普通变量，打包传给后台线程，线程里不再碰任何 Tk 对象。

**二、`gallery-dl` 的返回值是整数状态码，不是对象。** `DownloadJob.run()` 返回位掩码，`rc != 0` 才算失败。早先版本误当成 `.total / .success` 去取，结果是永远 0——日志显示「共下载 0/0 张」，失败也被算成成功。

**三、别拿函数的返回值当"副作用成功"的标志。** `build_options()` 只往 gallery-dl 的全局配置里写东西，没有返回值（隐式 `None`）。调用处拿它的返回值当"图片功能可用"的判断，于是 v2.3.0–2.3.2 所有图片下载都报「缺少依赖」，而依赖其实都在。这个 bug 是端到端测试第一次跑时才暴露的。

## 已知限制

**国内抖音下不了。** 链接能还原、登录态能拿到、`a_bogus` 签名能算、TLS 指纹能伪装，五层都过了，抖音的视频详情接口仍然返回 403。卡点在它页面 JS 里的会话密钥，纯 HTTP 客户端拿不到。

**Chrome / Edge 的 cookie 读不了。** 从 127 版起这两个浏览器用 App-Bound Encryption 保护 cookie，yt-dlp 只支持老式 DPAPI，报 `Failed to decrypt with DPAPI`。要登录态请用 Firefox，或者导入 Netscape 格式的 cookies 文件。

**只支持 Windows。** 打包脚本、路径处理都按 Windows 写的。

**单文件 EXE 启动慢两三秒**，每次要先解压。

## 许可

MIT，见 [LICENSE](LICENSE)。
