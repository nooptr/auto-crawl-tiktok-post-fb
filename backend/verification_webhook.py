import sys
import os

# Thêm đường dẫn thủ công tới ứng dụng backend
sys.path.append(r'c:\Users\Beemo\Downloads\social_tool\backend')

from app.services.fb_graph import upload_video_to_facebook

def test_tu_choi_webhook_url_cu():
    print("Đang kiểm tra cơ chế từ chối URL webhook cũ...")

    dummy_file = 'dummy_webhook.mp4'
    with open(dummy_file, 'w') as f:
        f.write('noi dung gia lap')
    
    try:
        res = upload_video_to_facebook(
            dummy_file,
            "Chú thích thử nghiệm",
            "page_123",
            "https://hook.us1.make.com/abc123xyz"
        )
        assert "error" in res
        assert "mã truy cập trang Facebook thật" in res["error"]
        print("Đã chặn đúng kiểu cấu hình webhook cũ.")
    finally:
        if os.path.exists(dummy_file):
            os.remove(dummy_file)

if __name__ == "__main__":
    try:
        test_tu_choi_webhook_url_cu()
    except Exception as e:
        print(f"Xác minh thất bại: {e}")
        sys.exit(1)
