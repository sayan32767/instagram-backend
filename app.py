from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import uuid
import os
from dotenv import load_dotenv
import cloudinary
import cloudinary.uploader
import base64
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

# ---------------- INIT ----------------
load_dotenv()
app = Flask(__name__)

# 🔒 Max upload size (200MB)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# 🔒 Rate limiter
limiter = Limiter(get_remote_address, app=app, default_limits=["60 per minute"])

# 🔒 Secret key for protected endpoints
API_SECRET = os.getenv("UPLOAD_SECRET")

# ----------- CLOUDINARY CONFIG ----------
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

# ----------- YOUTUBE HELPER ----------


def get_youtube_client():
    creds = Credentials(
        token=None,
        refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.getenv("YOUTUBE_CLIENT_ID"),
        client_secret=os.getenv("YOUTUBE_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    creds.refresh(Request())

    return build("youtube", "v3", credentials=creds)


# ---------------- HELPERS ----------------


def upload_generated_image(img_bytes: bytes, uid: str):
    try:
        childName = "generatedImages"
        image_id = str(uuid.uuid4())

        upload_result = cloudinary.uploader.upload(
            img_bytes,
            public_id=f"{childName}/{uid}/{image_id}",
            asset_folder=childName,
            resource_type="image",
            overwrite=True,
            unique_filename=False,
        )

        return upload_result["secure_url"]

    except Exception as e:
        print("Cloudinary error:", e)
        return None



# ---------------- GENERATE ROUTE ----------------


@app.route("/generate", methods=["GET"])
@limiter.limit("20 per minute")
def generate():
    try:
        prompt = request.args.get("prompt")
        if not prompt:
            return jsonify({"error": "Prompt required"}), 400
        
        uid = request.args.get("uid")
        if not uid:
            return jsonify({"error": "UID required"}), 400

        url = os.getenv("BASE_URL") + prompt
        response = requests.get(url, timeout=30)

        if response.status_code != 200:
            return jsonify({"error": "Image generation failed"}), 500

        cloudinary_url = upload_generated_image(response.content, uid=uid)

        if not cloudinary_url:
            return jsonify({"error": "Upload failed"}), 500

        return jsonify({
            "status": "success",
            "data": {"media": [{"url": cloudinary_url}]}
        })

    except Exception:
        return jsonify({"error": "Unexpected server error"}), 500


# ---------------- SPOTIFY TOKEN ----------------


def get_access_token():
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")

    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    b64_auth_str = base64.b64encode(auth_str.encode()).decode()

    headers = {
        "Authorization": f"Basic {b64_auth_str}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {"grant_type": "client_credentials"}

    response = requests.post(
        "https://accounts.spotify.com/api/token",
        headers=headers,
        data=data,
        timeout=15,
    )

    if response.status_code == 200:
        return response.json()["access_token"]

    raise Exception("Failed to get Spotify access token")


# ---------------- SPOTIFY SEARCH ----------------


@app.route("/search", methods=["GET"])
@limiter.limit("30 per minute")
def search_track():
    query = request.args.get("query")

    if not query:
        return jsonify({"error": "Query parameter is required"}), 400

    try:
        access_token = get_access_token()

        headers = {"Authorization": f"Bearer {access_token}"}
        search_url = f"https://api.spotify.com/v1/search?q={query}&type=track&limit=10"

        response = requests.get(search_url, headers=headers, timeout=15)

        if response.status_code != 200:
            return jsonify({"error": "Spotify search failed"}), 500

        results = response.json()["tracks"]["items"]

        track_data = [
            {
                "name": track["name"],
                "artist": ", ".join(a["name"] for a in track["artists"]),
                "album": track["album"]["name"],
                "album_art_url": track["album"]["images"][0]["url"]
                if track["album"]["images"] else None,
                "preview_url": track["preview_url"],
                "spotify_url": track["external_urls"]["spotify"],
            }
            for track in results
        ]

        return jsonify(track_data), 200

    except Exception:
        return jsonify({"error": "Server error"}), 500


# ---------------- YOUTUBE UPLOAD ----------------
@app.route("/upload-reel-yt", methods=["POST"])
@limiter.limit("5 per minute")
def upload_reel_yt():
    """Secure YouTube reel upload."""

    # 🔒 API key protection
    if request.headers.get("X-API-KEY") != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    if "video" not in request.files:
        return jsonify({"error": "Video file required"}), 400

    video = request.files["video"]

    # Save temp file
    temp_path = f"/tmp/{uuid.uuid4()}.mp4"
    video.save(temp_path)

    try:
        youtube = get_youtube_client()

        title = request.form.get("title", "New Reel")
        description = request.form.get("description", "")
        privacy = request.form.get("privacy", "unlisted")

        request_body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22",
            },
            "status": {"privacyStatus": privacy},
        }

        media = MediaFileUpload(temp_path, chunksize=-1, resumable=True)

        upload_request = youtube.videos().insert(
            part="snippet,status",
            body=request_body,
            media_body=media,
        )

        response = None

        # 🔁 resumable upload with progress
        while response is None:
            status, response = upload_request.next_chunk()
            if status:
                print(f"Upload progress: {int(status.progress() * 100)}%")

        return jsonify({
            "success": True,
            "videoId": response["id"],
            "youtubeUrl": f"https://youtu.be/{response['id']}",
        })

    # except Exception as e:
    #     return jsonify({"success": False, "error": str(e)}), 500
    except Exception as e:
        import traceback
        traceback.print_exc()   # 👈 prints real error in terminal
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        # 🧹 cleanup temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)


# ---------------- TELEGRAM UPLOAD ----------------

@app.route("/upload-reel", methods=["POST"])
def upload_reel():
    # 🔒 API key protection
    if request.headers.get("X-API-KEY") != API_SECRET:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    if "video" not in request.files:
        return jsonify({"success": False, "error": "Video file required"}), 400

    video = request.files["video"]
    caption = request.form.get("caption", "")

    # Step 1: upload to Telegram
    r = requests.post(
        f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}/sendVideo",
        data={"chat_id": os.getenv("CHAT_ID"), "caption": caption},
        files={"video": video},
    ).json()

    if not r.get("ok"):
        return jsonify({"success": False, "error": r}), 500

    # Step 2: extract file_id
    file_id = r["result"]["video"]["file_id"]

    # Step 3: return file_id to client for later retrieval
    return jsonify({
        "success": True,
        "file_id": file_id,
    })


#---------------- TELEGRAM VIDEO URL ----------------

@app.route("/video-url/<file_id>")
def get_video_url(file_id):
    try:
        # 🔹 Step 1: call Telegram getFile
        tg_res = requests.get(
            f"https://api.telegram.org/bot{os.getenv('BOT_TOKEN')}/getFile",
            params={"file_id": file_id},
            timeout=10,
        ).json()

        if not tg_res.get("ok"):
            return jsonify({"error": "Telegram getFile failed"}), 500

        file_path = tg_res["result"].get("file_path")

        if not file_path:
            return jsonify({"error": "file_path missing"}), 500

        # 🔹 Step 2: build playable CDN URL
        video_url = f"https://api.telegram.org/file/bot{os.getenv('BOT_TOKEN')}/{file_path}"

        return jsonify({"url": video_url})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ----------------- HEALTH CHECK ----------------
@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "reels-api"
    }), 200


# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
