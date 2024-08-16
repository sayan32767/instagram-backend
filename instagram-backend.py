from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
from meta_ai_api import MetaAI
from dotenv import load_dotenv
import os

load_dotenv()

fb_email = os.getenv('EMAIL')
fb_password = os.getenv('PASSWORD')

app = Flask(__name__)

# Configure Flask-Limiter
limiter = Limiter(
    key_func=get_remote_address,  # Use the client's IP address for rate limiting
    default_limits=["100 per hour"]  # Set a default rate limit for all routes
)
limiter.init_app(app)  # Initialize the limiter with the Flask app

def try_login():
    try:
        ai = MetaAI(fb_email=fb_email, fb_password=fb_password)
    except:
        return None
    else:
        return ai

@app.route('/generate', methods=['GET'])
@limiter.limit("1 per minute")  # Set a specific rate limit for this route
def get_data(): 
    try:
        ai = try_login()
        prompt = request.args.get('prompt')

        if ai is not None and prompt is not None:
            response = ai.prompt(message= 'Generate an image of a' + prompt)
        else:
            return None

    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
        return None
    
    else:
        result = {
            'status': 'success',
            'data': response
        }
        return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)