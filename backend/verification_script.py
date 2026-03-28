import sys
import os

# Thêm đường dẫn thủ công tới ứng dụng backend
sys.path.append(r'c:\Users\Beemo\Downloads\social_tool\backend')

from app.services.fb_graph import upload_video_to_facebook
from app.services.ai_generator import generate_caption

def test_fb_payload():
    print("Đang kiểm tra payload đăng Facebook...")
    # Giả lập requests.post
    import requests
    from unittest.mock import MagicMock
    
    responses = [
        MagicMock(json=MagicMock(return_value={'video_id': 'video_123'})),
        MagicMock(json=MagicMock(return_value={'success': True})),
        MagicMock(json=MagicMock(return_value={'success': True})),
    ]
    requests.post = MagicMock(side_effect=responses)
    
    # Tạo tệp giả để mô phỏng video
    dummy_file = 'dummy.mp4'
    with open(dummy_file, 'w') as f:
        f.write('noi dung gia lap')
    
    try:
        result = upload_video_to_facebook(dummy_file, "Chú thích thử nghiệm", "page_123", "token_123")

        assert requests.post.call_count == 3

        init_call = requests.post.call_args_list[0]
        assert init_call.args[0] == "https://graph.facebook.com/v19.0/page_123/video_reels"
        assert init_call.kwargs["params"]["upload_phase"] == "start"

        upload_call = requests.post.call_args_list[1]
        assert upload_call.args[0] == "https://rupload.facebook.com/video-upload/v19.0/video_123"
        assert upload_call.kwargs["headers"]["Authorization"] == "OAuth token_123"

        publish_call = requests.post.call_args_list[2]
        assert publish_call.kwargs["params"]["upload_phase"] == "finish"
        assert publish_call.kwargs["params"]["description"] == "Chú thích thử nghiệm"
        assert result.get("id") == "video_123"
        print("Kiểm tra payload đăng Facebook thành công!")
    finally:
        if os.path.exists(dummy_file):
            os.remove(dummy_file)

def test_ai_model_name():
    print("Đang kiểm tra tên mô hình AI...")
    # Xác nhận mã nguồn đang dùng đúng tên mô hình hiện tại
    import inspect
    source = inspect.getsource(generate_caption)
    print(f"Tìm thấy mô hình gemini-2.5-flash trong mã nguồn: {'gemini-2.5-flash' in source}")
    assert 'gemini-2.5-flash' in source
    assert 'gemini-1.5-flash' not in source
    print("Kiểm tra tên mô hình AI thành công!")

if __name__ == "__main__":
    try:
        test_fb_payload()
        test_ai_model_name()
    except Exception as e:
        print(f"Xác minh thất bại: {e}")
        sys.exit(1)
