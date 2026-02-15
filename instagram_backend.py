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

# ---------------- INIT ----------------
load_dotenv()

app = Flask(__name__)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per hour"]
)
limiter.init_app(app)

# ----------- CLOUDINARY CONFIG ----------
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
)

# ---------------- HELPERS ----------------

def upload_generated_image(img_bytes: bytes):
    """
    Upload image bytes directly to Cloudinary.
    Applies automatic resize + optimization.
    """

    try:
        upload_result = cloudinary.uploader.upload(
            img_bytes,
            public_id=f"generatedImages/{uuid.uuid4()}",
            resource_type="image",
            overwrite=True,
            transformation=[
                {"width": 768, "height": 768, "crop": "fill"},
                {"quality": "auto", "fetch_format": "auto"}
            ],
        )

        return upload_result["secure_url"]

    except Exception:
        return None


# ---------------- GENERATE ROUTE ----------------

@app.route("/generate", methods=["GET"])
@limiter.limit("1 per minute")
def generate():
    try:
        prompt = request.args.get("prompt")
        if not prompt:
            return jsonify({"error": "Prompt required"}), 400

        url = os.getenv("BASE_URL") + prompt
        response = requests.get(url, timeout=30)

        if response.status_code != 200:
            return jsonify({"error": "Image generation failed"}), 500

        # Upload to Cloudinary
        cloudinary_url = upload_generated_image(response.content)

        if not cloudinary_url:
            return jsonify({"error": "Upload failed"}), 500

        return jsonify({
            "status": "success",
            "data": {
                "media": [{"url": cloudinary_url}]
            }
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
        print("Spotify access token obtained successfully")
        print(response.json()["access_token"])
        return response.json()["access_token"]

    raise Exception("Failed to get Spotify access token")


# ---------------- SPOTIFY SEARCH ----------------

@app.route("/search", methods=["GET"])
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
                "preview_url": track["preview_url"],  # may be null
                "spotify_url": track["external_urls"]["spotify"],
            }
            for track in results
        ]

        return jsonify(track_data), 200

    except Exception:
        return jsonify({"error": "Server error"}), 500


# ---------------- RUN ----------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)
