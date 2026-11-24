"""业务逻辑层：yt-dlp 封装（解析 / 下载）。不依赖 Streamlit，便于测试。

- ``extractor.py``  视频元信息与清晰度列表
- ``downloader.py`` 下载任务、进度回调、FFmpeg 合并、取消
- ``utils.py``      时长/体积/速度格式化、文件名清洗、日志静默器
"""
