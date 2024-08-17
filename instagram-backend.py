from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
import firebase_admin
from firebase_admin import credentials
from firebase_admin import storage
import uuid
from datetime import timedelta
from dotenv import load_dotenv
import os
from PIL import Image
from io import BytesIO

app = Flask(__name__)

def init_firebase_app():
    try:
        cred = credentials.Certificate('my_secret_config.json')
        storage_bucket = os.getenv('STORAGE_BUCKET')
        
        if storage_bucket is None:
            return
            
        firebase_admin.initialize_app(cred, {
            'storageBucket': storage_bucket
        })
    except:
        return
    
def resize_image(image_bytes: bytes, new_width: int, new_height: int) -> bytes:
    image = Image.open(BytesIO(image_bytes))
    
    resized_image = image.resize((new_width, new_height))
    
    buffer = BytesIO()
    resized_image.save(buffer, format=image.format)
    
    return buffer.getvalue()

def get_url(img_data):
    try:
        img_data = resize_image(image_bytes=img_data, new_width=768, new_height=768)

        bucket = storage.bucket()

        blob = bucket.blob(f'generatedImages/{uuid.uuid4()}')
        
        blob.upload_from_string(img_data, content_type='image/jpeg')

        expiration_time = timedelta(hours=1)

        url = blob.generate_signed_url(expiration=expiration_time)
    except:
        return None
    else:
        return url

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100 per hour"]
)

load_dotenv()

limiter.init_app(app)

init_firebase_app()

@app.route('/generate', methods=['GET'])
@limiter.limit("1 per minute")
def get_data(): 
    try:
        prompt = request.args.get('prompt')

        if prompt is None:
            return None
        
        url = os.getenv('BASE_URL') + prompt

        if url is None:
            return None
        
        response = requests.get(url)

    except:
        return None
    
    else:
        if response.status_code != 200:
            return None
        
        download_url = get_url(response.content)

        if download_url is None:
            return None

        result = {
            'status': 'success',
            'data': {
                'media': [
                    {
                        'url': download_url
                    }
                ]
            }
        }
        return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
