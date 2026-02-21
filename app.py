from flask import Flask, request, jsonify, Blueprint
import firebase_admin
from firebase_admin import credentials, firestore, auth
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
import bcrypt

# ---------------- INIT ----------------
load_dotenv()
app = Flask(__name__)

# 🔒 Max upload size (10MB)
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", 50))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# 🔒 Rate limiter
limiter = Limiter(get_remote_address, app=app, default_limits=["60 per minute"])

# 🔒 Secret key for protected endpoints
API_SECRET = os.getenv("UPLOAD_SECRET")






# ---------------- FIREBASE INIT ----------------

firebase_admin.initialize_app(
    credentials.Certificate('serviceAccountKey.json')
)

group_bp = Blueprint("group", __name__)
db = firestore.client()





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

    # 🔒 Validate file type
    filename = video.filename
    ext = os.path.splitext(filename)[1].lower()

    allowed = {".mp4", ".mov", ".avi", ".mkv"}

    if video.filename == "":
        return jsonify({"success": False, "error": "No file selected"}), 400

    if ext not in allowed:
        return jsonify({"success": False, "error": "Invalid file type"}), 400

    # 🔒 Check file size safely
    video.seek(0, os.SEEK_END)
    file_size = video.tell()
    video.seek(0)  # reset pointer back to start

    max_size_mb = int(os.getenv("TELEGRAM_SIZE_LIMIT", 10))
    max_size = max_size_mb * 1024 * 1024  # convert MB to bytes

    if file_size > max_size:
        return jsonify({
            "success": False,
            "error": f"Video too large. Max allowed is {max_size_mb} MB."
        }), 413


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






# ----------------- FIREBASE UPLOAD ----------------
def verify_token():
    id_token = request.headers.get("Authorization")

    if not id_token:
        return None, ("Missing token", 401)

    try:
        decoded = auth.verify_id_token(id_token)
        return decoded["uid"], None
    except Exception:
        return None, ("Invalid token", 401)


# -------------------------
# 🚀 CREATE GROUP
# -------------------------
@group_bp.route("/create-group", methods=["POST"])
@limiter.limit("5 per minute")
def create_group():
    if request.headers.get("X-API-KEY") != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    uid, error = verify_token()
    if error:
        return jsonify({"error": error[0]}), error[1]

    data = request.json
    name = data.get("name", "").strip().lower()
    password = data.get("password", "")

    if not name or not password:
        return jsonify({"error": "Missing fields"}), 400

    if len(password) < 8:
        return jsonify({"error": "Password too short"}), 400

    hashed_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    user_doc = db.collection("user").document(uid).get()
    if not user_doc.exists:
        return jsonify({"error": "User not found"}), 404

    user_data = user_doc.to_dict()
    username = user_data.get("username", "")
    photo_url = user_data.get("photoUrl", "")

    group_ref = db.collection("groups").document(name)

    tx = db.transaction()

    @firestore.transactional
    def transaction_create(tx):
        group_snapshot = next(tx.get(group_ref))

        # 🔥 Atomic uniqueness check
        if group_snapshot.exists:
            raise Exception("Group name already taken")

        tx.set(group_ref, {
            "name": name,
            "passwordHash": hashed_pw,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "createdBy": {
                "uid": uid,
                "username": username,
                "photoUrl": photo_url,
            },
        })

        tx.set(
            group_ref.collection("members").document(uid),
            {
                "role": "member",
                "username": username,
                "photoUrl": photo_url,
                "joinedAt": firestore.SERVER_TIMESTAMP,
            }
        )

        tx.set(
            db.collection("user")
            .document(uid)
            .collection("groups")
            .document(name),
            {
                "name": name,
                "username": username,
                "photoUrl": photo_url,
                "role": "member",
                "joinedAt": firestore.SERVER_TIMESTAMP,
            }
        )

    try:
        transaction_create(tx)
        return jsonify({"success": True, "groupId": name}), 200
    except Exception as e:
        if "taken" in str(e):
            return jsonify({"error": "Group name already taken"}), 400
        print("Error creating group:", e)
        return jsonify({"error": "Server error"}), 500


# -------------------------
# 🚀 JOIN GROUP
# -------------------------
@group_bp.route("/join-group", methods=["POST"])
@limiter.limit("10 per minute")
def join_group():
    if request.headers.get("X-API-KEY") != API_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    uid, error = verify_token()
    if error:
        return jsonify({"error": error[0]}), error[1]

    data = request.json
    name = data.get("name", "").strip().lower()
    password = data.get("password", "")

    if not name or not password:
        return jsonify({"error": "Missing fields"}), 400

    group_ref = db.collection("groups").document(name)
    group_doc = group_ref.get()

    if not group_doc.exists:
        return jsonify({"error": "Group not found"}), 404

    group_data = group_doc.to_dict()
    stored_hash = group_data.get("passwordHash")

    if not stored_hash:
        return jsonify({"error": "Corrupted group"}), 500

    if not bcrypt.checkpw(password.encode(), stored_hash.encode()):
        return jsonify({"error": "Incorrect password"}), 403

    user_doc = db.collection("user").document(uid).get()
    if not user_doc.exists:
        return jsonify({"error": "User not found"}), 404

    user_data = user_doc.to_dict()
    username = user_data.get("username", "")
    photo_url = user_data.get("photoUrl", "")

    tx = db.transaction()

    @firestore.transactional
    def transaction_join(tx):
        member_ref = group_ref.collection("members").document(uid)
        user_group_ref = (
            db.collection("user")
            .document(uid)
            .collection("groups")
            .document(name)
        )

        # 🔥 ALL READS FIRST
        member_snapshot = next(tx.get(member_ref))
        user_group_snapshot = next(tx.get(user_group_ref))

        if not member_snapshot.exists:
            tx.set(member_ref, {
                "role": "member",
                "username": username,
                "photoUrl": photo_url,
                "joinedAt": firestore.SERVER_TIMESTAMP,
            })

        if not user_group_snapshot.exists:
            tx.set(user_group_ref, {
                "name": name,
                "username": username,
                "photoUrl": photo_url,
                "role": "member",
                "joinedAt": firestore.SERVER_TIMESTAMP,
            })

    try:
        transaction_join(tx)
        return jsonify({"success": True, "groupId": name}), 200
    except Exception as e:
        print("Error joining group:", e)
        return jsonify({"error": "Server error"}), 500




# --------------------- REGISTER BLUEPRINTS ----------------
app.register_blueprint(group_bp)




# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
