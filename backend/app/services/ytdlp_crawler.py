import yt_dlp
import os
import uuid
from pathlib import Path

from app.core.config import settings

DOWNLOAD_DIR = settings.DOWNLOAD_DIR

def extract_metadata(url: str):
    ydl_opts = {
        'skip_download': True,
        'quiet': True,
        'extract_flat': False,  # Lấy đầy đủ thông tin metadata
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # Nếu URL là trang hồ sơ người dùng, hàm sẽ trả về danh sách video
        info = ydl.extract_info(url, download=False)
        return info

def download_video(url: str, filename_prefix: str = "video"):
    Path(DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)
    video_id = str(uuid.uuid4())
    filename = f"{filename_prefix}_{video_id}.mp4"
    out_path = os.path.join(DOWNLOAD_DIR, filename)

    ydl_opts = {
        'format': 'best[vcodec^=h264]/best[vcodec^=avc]/best',  # Ưu tiên H.264/AVC để Facebook xử lý ổn định hơn
        'outtmpl': out_path,
        'quiet': True,
        'no_warnings': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return out_path, video_id
    except Exception as e:
        print(f"Lỗi tải video {url}: {e}")
        return None, None
