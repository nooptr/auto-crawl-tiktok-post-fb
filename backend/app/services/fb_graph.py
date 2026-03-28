import requests
import os
import time

GRAPH_API_BASE = "https://graph.facebook.com/v19.0"

def upload_video_to_facebook(file_path: str, caption: str, page_id: str, access_token: str):
    """ 
    Tải video trực tiếp lên Facebook Reels bằng Graph API 3 bước (khởi tạo -> tải lên -> công bố).
    Quy trình này ổn định hơn nhiều cho Reels so với việc đẩy file 1 lần.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Không tìm thấy file: {file_path}")

    # Nếu người dùng vẫn nhập URL webhook cũ, báo lỗi yêu cầu đổi sang mã truy cập thật
    if access_token.startswith("http://") or access_token.startswith("https://"):
        return {'error': 'Vui lòng thay URL webhook bằng mã truy cập trang Facebook thật trong cấu hình.'}

    try:
        # Giai đoạn 1: Khởi tạo
        print(f"FB GHI CHÚ: Khởi tạo tải Reels cho trang {page_id}...")
        init_url = f"{GRAPH_API_BASE}/{page_id}/video_reels"
        params = {
            'upload_phase': 'start',
            'access_token': access_token
        }
        res_init = requests.post(init_url, params=params, timeout=30)
        res_init_data = res_init.json()
        
        if 'video_id' not in res_init_data:
            print(f"FB LỖI (khởi tạo - toàn bộ JSON): {res_init_data}")
            return {'error': f"Lỗi khởi tạo Reels: {res_init_data.get('error', {}).get('message', 'Lỗi không xác định')}"}
        
        video_id = res_init_data['video_id']
        print(f"FB GHI CHÚ: Đã lấy mã video: {video_id}")

        # Giai đoạn 2: Tải lên - Dùng hạ tầng RUpload chuyên dụng
        print("FB GHI CHÚ: Đang tải dữ liệu video lên hạ tầng RUpload...")
        upload_url = f"https://rupload.facebook.com/video-upload/v19.0/{video_id}"
        
        file_size = os.path.getsize(file_path)
        with open(file_path, 'rb') as f:
            headers = {
                'Authorization': f'OAuth {access_token}',
                'offset': '0',
                'file_size': str(file_size),
                'X-Entity-Type': 'video/mp4',
                'X-Entity-Name': 'video.mp4'
            }
            # Tải toàn bộ tệp lên rupload
            res_upload = requests.post(
                upload_url, 
                data=f, 
                headers=headers,
                timeout=300 # Cho phép 5 phút để tải lên
            )
        
        res_upload_data = res_upload.json()
        # Chú ý: RUpload có thể trả về thành công theo kiểu khác, nên kiểm tra 'id' hoặc 'success'
        if 'id' not in res_upload_data and not res_upload_data.get('success'):
            print(f"FB LỖI (RUpload - toàn bộ JSON): {res_upload_data}")
            return {'error': f"Lỗi tải video (RUpload): {res_upload_data.get('error', {}).get('message', 'Tải video thất bại')}"}

        # Giai đoạn 3: Hoàn tất và công bố
        print("FB GHI CHÚ: Đợi 20 giây để Facebook xử lý video trước khi công bố...")
        time.sleep(20) # Thời gian chờ rất quan trọng cho video lớn

        print("FB GHI CHÚ: Đang hoàn tất và công bố Reel...")
        publish_url = f"{GRAPH_API_BASE}/{page_id}/video_reels"
        publish_params = {
            'upload_phase': 'finish',
            'video_id': video_id,
            'video_state': 'PUBLISHED',
            'description': caption,
            'access_token': access_token
        }
        res_publish = requests.post(publish_url, params=publish_params, timeout=30)
        res_publish_data = res_publish.json()

        if 'success' in res_publish_data and res_publish_data['success']:
            print(f"FB THÀNH CÔNG: Đã đăng Reel thành công. Mã video: {video_id}")
            return {'id': video_id}
        else:
            print(f"FB LỖI (công bố - toàn bộ JSON): {res_publish_data}")
            error_msg = res_publish_data.get('error', {}).get('message', 'Công bố thất bại')
            return {'error': f"Lỗi công bố: {error_msg}"}

    except Exception as e:
        print(f"FB LỖI NGHIÊM TRỌNG: {str(e)}")
        return {'error': f"Lỗi hệ thống khi đăng FB: {str(e)}"}


def inspect_page_access(page_id: str, access_token: str):
    url = f"{GRAPH_API_BASE}/{page_id}"
    params = {
        "fields": "id,name,link,fan_count",
        "access_token": access_token,
    }
    try:
        response = requests.get(url, params=params, timeout=30)
        data = response.json()
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Không thể kết nối tới Facebook Graph API: {exc}",
        }

    if "error" in data:
        return {
            "ok": False,
            "message": data["error"].get("message", "Mã truy cập không hợp lệ hoặc chưa đủ quyền."),
        }

    return {
        "ok": True,
        "message": f"Mã truy cập hợp lệ cho trang Facebook {data.get('name', page_id)}.",
        "page_id": data.get("id", page_id),
        "page_name": data.get("name"),
        "page_link": data.get("link"),
        "fan_count": data.get("fan_count"),
    }

def reply_to_comment(comment_id: str, message: str, access_token: str):
    """Trả lời bình luận thông qua Graph API."""
    url = f"https://graph.facebook.com/v19.0/{comment_id}/comments"
    data = {
        'message': message,
        'access_token': access_token
    }
    try:
        res = requests.post(url, data=data, timeout=30)
        res_data = res.json()
        if 'error' in res_data:
            print(f"FB LỖI GRAPH: {res_data['error'].get('message')}")
        return res_data
    except Exception as e:
        print(f"Lỗi API trả lời Facebook: {e}")
        return None
