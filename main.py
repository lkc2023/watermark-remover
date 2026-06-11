"""
视频去水印工具 - 整合版
支持：短视频解析 + 上传视频去水印
"""
import re
import os
import uuid
import shutil
import tempfile
import subprocess
import httpx
import numpy as np
from pathlib import Path
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# 创建FastAPI应用
app = FastAPI(title="视频去水印工具", version="2.0.0")

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置
UPLOAD_DIR = Path("../uploads")
OUTPUT_DIR = Path("../outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_VIDEO_SIZE = 100 * 1024 * 1024
ALLOWED_IMAGE = {"image/png", "image/jpeg", "image/webp", "image/bmp"}
ALLOWED_VIDEO = {"video/mp4", "video/avi", "video/mov", "video/mkv", "video/webm"}


def gen_id():
    return uuid.uuid4().hex[:12]

def get_ext(filename: str) -> str:
    return Path(filename).suffix.lower() or ".png"


# ─── 短视频解析 ──────────────────────────────────────────────

async def parse_douyin(url: str) -> dict:
    try:
        video_id = re.search(r'video/(\d+)', url)
        if not video_id:
            video_id = re.search(r'v\.douyin\.com/(\w+)', url)
        if not video_id:
            return {"success": False, "error": "无法解析抖音链接"}
        video_id = video_id.group(1)
        api_url = f"https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/?item_ids={video_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15"}
            response = await client.get(api_url, headers=headers)
            data = response.json()
            if data.get("item_list"):
                item = data["item_list"][0]
                video_url = item["video"]["play_addr"]["url_list"][0]
                title = item.get("desc", "抖音视频")
                return {"success": True, "platform": "抖音", "title": title, "video_url": video_url.replace("playwm", "play"), "cover": item["video"]["cover"]["url_list"][0]}
            return {"success": False, "error": "获取视频信息失败"}
    except Exception as e:
        return {"success": False, "error": f"解析抖音失败: {str(e)}"}

async def parse_kuaishou(url: str) -> dict:
    try:
        video_id = re.search(r'v\.kuaishou\.com/(\w+)', url)
        if not video_id:
            video_id = re.search(r'photo/(\d+)', url)
        if not video_id:
            return {"success": False, "error": "无法解析快手链接"}
        api_url = f"https://m.gifshow.com/rest/wd/photo/info?photoId={video_id.group(1)}&is498=true"
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15"}
            response = await client.get(api_url, headers=headers)
            data = response.json()
            if data.get("photo"):
                photo = data["photo"]
                return {"success": True, "platform": "快手", "title": photo.get("caption", "快手视频"), "video_url": photo["mainMvUrl"], "cover": photo["coverUrl"]}
            return {"success": False, "error": "获取视频信息失败"}
    except Exception as e:
        return {"success": False, "error": f"解析快手失败: {str(e)}"}

async def parse_xiaohongshu(url: str) -> dict:
    try:
        note_id = re.search(r'explore/(\w+)', url)
        if not note_id:
            note_id = re.search(r'discovery/item/(\w+)', url)
        if not note_id:
            return {"success": False, "error": "无法解析小红书链接"}
        api_url = f"https://www.xiaohongshu.com/explore/{note_id.group(1)}"
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15"}
            response = await client.get(api_url, headers=headers)
            html = response.text
            video_match = re.search(r'"videoUrl":"(.*?)"', html)
            title_match = re.search(r'"title":"(.*?)"', html)
            if video_match:
                video_url = video_match.group(1).replace("\\u002F", "/")
                title = title_match.group(1) if title_match else "小红书视频"
                return {"success": True, "platform": "小红书", "title": title, "video_url": video_url, "cover": ""}
            return {"success": False, "error": "获取视频信息失败"}
    except Exception as e:
        return {"success": False, "error": f"解析小红书失败: {str(e)}"}

async def parse_bilibili(url: str) -> dict:
    try:
        bv_id = re.search(r'BV(\w+)', url)
        if not bv_id:
            return {"success": False, "error": "无法解析B站链接"}
        bv_id = "BV" + bv_id.group(1)
        api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={bv_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15"}
            response = await client.get(api_url, headers=headers)
            data = response.json()
            if data.get("data"):
                video_data = data["data"]
                return {"success": True, "platform": "B站", "title": video_data.get("title", "B站视频"), "video_url": f"https://www.bilibili.com/video/{bv_id}", "cover": video_data.get("pic", "")}
            return {"success": False, "error": "获取视频信息失败"}
    except Exception as e:
        return {"success": False, "error": f"解析B站失败: {str(e)}"}

async def parse_weibo(url: str) -> dict:
    try:
        weibo_id = re.search(r'(\d+)', url.split('/')[-1])
        if not weibo_id:
            return {"success": False, "error": "无法解析微博链接"}
        api_url = f"https://m.weibo.cn/statuses/show?id={weibo_id.group(1)}"
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15"}
            response = await client.get(api_url, headers=headers)
            data = response.json()
            if data.get("data"):
                status = data["data"]
                if status.get("page_info", {}).get("type") == "video":
                    video_url = status["page_info"]["media_info"]["stream_url"]
                    return {"success": True, "platform": "微博", "title": status.get("text", "微博视频")[:50], "video_url": video_url, "cover": status.get("page_info", {}).get("page_pic", {}).get("url", "")}
            return {"success": False, "error": "获取视频信息失败"}
    except Exception as e:
        return {"success": False, "error": f"解析微博失败: {str(e)}"}

def detect_platform(url: str) -> str:
    if "douyin.com" in url or "iesdouyin.com" in url:
        return "douyin"
    elif "kuaishou.com" in url or "gifshow.com" in url:
        return "kuaishou"
    elif "xiaohongshu.com" in url or "xhslink.com" in url:
        return "xiaohongshu"
    elif "bilibili.com" in url or "b23.tv" in url:
        return "bilibili"
    elif "weibo.com" in url or "weibo.cn" in url:
        return "weibo"
    else:
        return "unknown"


# ─── 水印检测与去除 ──────────────────────────────────────────

def detect_watermark_advanced(img):
    import cv2
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    kernel_small = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    kernel_large = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    
    lower_white = np.array([0, 0, 160])
    upper_white = np.array([180, 80, 255])
    white_mask = cv2.inRange(hsv, lower_white, upper_white)
    white_mask = cv2.dilate(white_mask, kernel_large, iterations=3)
    
    v_channel = hsv[:, :, 2]
    blur_v = cv2.GaussianBlur(v_channel, (51, 51), 0)
    brightness_diff = cv2.absdiff(v_channel, blur_v)
    _, bright_mask = cv2.threshold(brightness_diff, 15, 255, cv2.THRESH_BINARY)
    bright_mask = cv2.dilate(bright_mask, kernel_large, iterations=2)
    
    edges = cv2.Canny(gray, 30, 100)
    edges = cv2.dilate(edges, kernel_small, iterations=2)
    
    regions = [
        (0, int(w*0.6), int(h*0.25), w),
        (0, 0, int(h*0.25), int(w*0.4)),
        (int(h*0.75), int(w*0.6), h, w),
        (int(h*0.75), 0, h, int(w*0.4)),
    ]
    region_mask = np.zeros((h, w), dtype=np.uint8)
    for y1, x1, y2, x2 in regions:
        roi = hsv[y1:y2, x1:x2]
        roi_white = cv2.inRange(roi, lower_white, upper_white)
        roi_white = cv2.dilate(roi_white, kernel_large, iterations=3)
        region_mask[y1:y2, x1:x2] = cv2.bitwise_or(region_mask[y1:y2, x1:x2], roi_white)
    
    adaptive = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 5)
    adaptive = cv2.dilate(adaptive, kernel_small, iterations=2)
    
    mask = cv2.bitwise_or(mask, white_mask)
    mask = cv2.bitwise_or(mask, bright_mask)
    mask = cv2.bitwise_or(mask, edges)
    mask = cv2.bitwise_or(mask, region_mask)
    mask = cv2.bitwise_or(mask, adaptive)
    mask = cv2.dilate(mask, kernel_large, iterations=3)
    
    center_mask = np.zeros((h, w), dtype=np.uint8)
    margin_h = int(h * 0.25)
    margin_w = int(w * 0.25)
    center_mask[margin_h:h-margin_h, margin_w:w-margin_w] = 255
    edge_only = cv2.bitwise_and(mask, cv2.bitwise_not(center_mask))
    obvious_white = cv2.inRange(hsv, np.array([0, 0, 210]), np.array([180, 30, 255]))
    obvious_white = cv2.dilate(obvious_white, kernel_small, iterations=2)
    mask = cv2.bitwise_or(edge_only, cv2.bitwise_and(obvious_white, cv2.bitwise_not(center_mask)))
    
    return mask

def remove_watermark_local(image_path: str, output_path: str) -> str:
    import cv2
    img_path_fixed = image_path.replace("\\", "/")
    out_path_fixed = output_path.replace("\\", "/")
    img = cv2.imread(img_path_fixed)
    if img is None:
        raise ValueError(f"无法读取图片: {image_path}")
    mask = detect_watermark_advanced(img)
    result = cv2.inpaint(img, mask, inpaintRadius=9, flags=cv2.INPAINT_TELEA)
    cv2.imwrite(out_path_fixed, result)
    return output_path

def remove_watermark_video(video_path: str, output_path: str, provider: str = "local", watermark_type: str = "auto", mask_path: str = None) -> str:
    import cv2
    ascii_temp_dir = "C:\\temp_wm"
    os.makedirs(ascii_temp_dir, exist_ok=True)
    temp_dir = tempfile.mkdtemp(dir=ascii_temp_dir)
    temp_video = os.path.join(temp_dir, "input.mp4")
    frames_dir = os.path.join(temp_dir, "frames")
    processed_dir = os.path.join(temp_dir, "processed")
    temp_output = os.path.join(temp_dir, "output.mp4")
    os.makedirs(frames_dir, exist_ok=True)
    os.makedirs(processed_dir, exist_ok=True)
    
    # 读取用户提供的mask（如果有）
    user_mask = None
    if mask_path and os.path.exists(mask_path):
        user_mask = cv2.imread(mask_path.replace("\\", "/"), cv2.IMREAD_GRAYSCALE)
        if user_mask is not None:
            # 膨胀mask
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
            user_mask = cv2.dilate(user_mask, kernel, iterations=3)
    
    try:
        shutil.copy2(video_path, temp_video)
        frame_pattern = os.path.join(frames_dir, "frame_%06d.png")
        cmd_extract = ["ffmpeg", "-i", temp_video, "-vf", "fps=10", frame_pattern, "-y"]
        result = subprocess.run(cmd_extract, capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"视频帧提取失败: {result.stderr[:200]}")
        
        extracted_frames = sorted([f for f in os.listdir(frames_dir) if f.endswith(".png")])
        for i, frame_name in enumerate(extracted_frames):
            frame_file = os.path.join(frames_dir, frame_name)
            out_frame = os.path.join(processed_dir, frame_name)
            try:
                img = cv2.imread(frame_file.replace("\\", "/"))
                if img is None:
                    shutil.copy2(frame_file, out_frame)
                    continue
                
                # 使用用户提供的mask或自动检测
                if user_mask is not None:
                    # 调整mask大小匹配帧
                    if user_mask.shape[:2] != img.shape[:2]:
                        mask_resized = cv2.resize(user_mask, (img.shape[1], img.shape[0]))
                    else:
                        mask_resized = user_mask
                    result = cv2.inpaint(img, mask_resized, inpaintRadius=9, flags=cv2.INPAINT_TELEA)
                else:
                    mask = detect_watermark_advanced(img)
                    result = cv2.inpaint(img, mask, inpaintRadius=9, flags=cv2.INPAINT_TELEA)
                
                cv2.imwrite(out_frame.replace("\\", "/"), result)
            except Exception as e:
                print(f"处理帧 {frame_name} 失败: {e}")
                shutil.copy2(frame_file, out_frame)
            if i % 10 == 0:
                print(f"  处理帧: {i+1}/{len(extracted_frames)}")
        
        processed_pattern = os.path.join(processed_dir, "frame_%06d.png")
        cmd_compose = ["ffmpeg", "-framerate", "10", "-i", processed_pattern, "-i", temp_video, "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "copy", "-shortest", temp_output, "-y"]
        result = subprocess.run(cmd_compose, capture_output=True, text=True)
        if result.returncode != 0:
            raise ValueError(f"视频合成失败: {result.stderr[:200]}")
        
        shutil.copy2(temp_output, output_path)
        return output_path
    finally:
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass


# ─── API 路由 ──────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"message": "视频去水印工具", "version": "2.0.0", "features": ["短视频解析", "上传去水印"]}

@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")

@app.post("/api/parse")
async def parse_video(url: str = Form(...)):
    platform = detect_platform(url)
    if platform == "unknown":
        raise HTTPException(400, "不支持的平台，请输入抖音/快手/小红书/B站/微博的视频链接")
    parsers = {"douyin": parse_douyin, "kuaishou": parse_kuaishou, "xiaohongshu": parse_xiaohongshu, "bilibili": parse_bilibili, "weibo": parse_weibo}
    result = await parsers[platform](url)
    if result["success"]:
        return JSONResponse(content=result)
    else:
        raise HTTPException(500, result["error"])

@app.post("/api/remove-image")
async def remove_image_watermark(file: UploadFile = File(...), provider: str = Form(default="local"), watermark_type: str = Form(default="auto")):
    ext = get_ext(file.filename or "").lower()
    is_image_ext = ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    is_image_type = file.content_type in ALLOWED_IMAGE
    if not is_image_ext and not is_image_type:
        raise HTTPException(400, f"不支持的图片格式: {file.content_type} ({ext})")
    content = await file.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(400, f"图片大小超过限制 ({MAX_IMAGE_SIZE // 1024 // 1024}MB)")
    file_id = gen_id()
    ext = get_ext(file.filename or "image.png")
    input_path = UPLOAD_DIR / f"{file_id}{ext}"
    output_path = OUTPUT_DIR / f"{file_id}_clean{ext}"
    with open(input_path, "wb") as f:
        f.write(content)
    try:
        remove_watermark_local(str(input_path), str(output_path))
        return FileResponse(str(output_path), media_type=file.content_type, filename=f"clean_{file.filename or 'image.png'}")
    except Exception as e:
        raise HTTPException(500, f"处理失败: {str(e)}")


@app.post("/api/remove-image-annotated")
async def remove_image_annotated(file: UploadFile = File(...), mask: UploadFile = File(...)):
    """手动标注去水印"""
    import cv2
    import tempfile
    import shutil
    
    # 使用临时目录避免中文路径问题
    temp_dir = tempfile.mkdtemp(dir="C:\\temp_wm")
    
    try:
        # 保存原图到临时目录
        input_path = os.path.join(temp_dir, "input.png")
        content = await file.read()
        with open(input_path, "wb") as f:
            f.write(content)
        
        # 保存mask到临时目录
        mask_path = os.path.join(temp_dir, "mask.png")
        mask_content = await mask.read()
        with open(mask_path, "wb") as f:
            f.write(mask_content)
        
        output_path = os.path.join(temp_dir, "output.png")
        
        # 读取图片和mask
        img = cv2.imread(input_path)
        mask_img = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        
        if img is None:
            raise ValueError("无法读取图片，请检查格式")
        if mask_img is None:
            raise ValueError("无法读取mask")
        
        # 调整mask大小匹配原图
        if mask_img.shape[:2] != img.shape[:2]:
            mask_img = cv2.resize(mask_img, (img.shape[1], img.shape[0]))
        
        # 膨胀mask以覆盖更多区域
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask_img = cv2.dilate(mask_img, kernel, iterations=3)
        
        # 使用inpainting修复
        result = cv2.inpaint(img, mask_img, inpaintRadius=9, flags=cv2.INPAINT_TELEA)
        
        # 保存结果
        cv2.imwrite(output_path, result)
        
        # 返回结果文件
        file_id = gen_id()
        ext = get_ext(file.filename or "image.png")
        final_path = OUTPUT_DIR / f"{file_id}_clean{ext}"
        shutil.copy2(output_path, str(final_path))
        
        return FileResponse(str(final_path), media_type="image/png", filename=f"clean_{file.filename or 'image.png'}")
    except Exception as e:
        raise HTTPException(500, f"处理失败: {str(e)}")
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass


@app.post("/api/remove-video-annotated")
async def remove_video_annotated(file: UploadFile = File(...), mask: UploadFile = File(...)):
    """手动标注视频去水印"""
    import cv2
    import tempfile
    import shutil
    
    # 使用临时目录避免中文路径问题
    temp_dir = tempfile.mkdtemp(dir="C:\\temp_wm")
    
    try:
        # 保存原视频到临时目录
        input_path = os.path.join(temp_dir, "input.mp4")
        content = await file.read()
        with open(input_path, "wb") as f:
            f.write(content)
        
        # 保存mask到临时目录
        mask_path = os.path.join(temp_dir, "mask.png")
        mask_content = await mask.read()
        with open(mask_path, "wb") as f:
            f.write(mask_content)
        
        output_path = os.path.join(temp_dir, "output.mp4")
        
        # 处理视频
        remove_watermark_video(input_path, output_path, mask_path=mask_path)
        
        # 返回结果
        file_id = gen_id()
        final_path = OUTPUT_DIR / f"{file_id}_clean.mp4"
        shutil.copy2(output_path, str(final_path))
        
        return FileResponse(str(final_path), media_type="video/mp4", filename=f"clean_{file.filename or 'video.mp4'}")
    except Exception as e:
        raise HTTPException(500, f"处理失败: {str(e)}")
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass


@app.post("/api/remove-video")
async def remove_video_watermark(file: UploadFile = File(...), provider: str = Form(default="local"), watermark_type: str = Form(default="auto")):
    ext = get_ext(file.filename or "").lower()
    is_video_ext = ext in {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv"}
    is_video_type = file.content_type in ALLOWED_VIDEO
    if not is_video_ext and not is_video_type:
        raise HTTPException(400, f"不支持的视频格式: {file.content_type} ({ext})")
    content = await file.read()
    if len(content) > MAX_VIDEO_SIZE:
        raise HTTPException(400, f"视频大小超过限制 ({MAX_VIDEO_SIZE // 1024 // 1024}MB)")
    file_id = gen_id()
    input_path = UPLOAD_DIR / f"{file_id}.mp4"
    output_path = OUTPUT_DIR / f"{file_id}_clean.mp4"
    with open(input_path, "wb") as f:
        f.write(content)
    try:
        remove_watermark_video(str(input_path), str(output_path), provider, watermark_type)
        return FileResponse(str(output_path), media_type="video/mp4", filename=f"clean_{file.filename or 'video.mp4'}")
    except Exception as e:
        print(f"视频处理错误: {e}")
        raise HTTPException(500, f"处理失败: {str(e)}")
    finally:
        input_path.unlink(missing_ok=True)


# ─── 挂载前端静态文件 ──────────────────────────────────────────
frontend_dir = Path(__file__).parent
if (frontend_dir / "index.html").exists():
    app.mount("/static", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("🎬 视频去水印工具 - 整合版")
    print("=" * 50)
    print()
    print("📌 访问地址: http://localhost:9099")
    print()
    print("✨ 功能:")
    print("   - 短视频解析（抖音/快手/小红书/B站/微博）")
    print("   - 上传图片去水印")
    print("   - 上传视频去水印")
    print()
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=9099)
