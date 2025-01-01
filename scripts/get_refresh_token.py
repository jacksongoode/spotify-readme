import base64
import logging
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote, urlencode

import requests
from dotenv import load_dotenv
import sys

# Load environment variables
load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8000/callback/"

# All available Spotify API scopes
SCOPES = [
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-top-read",
    "user-read-recently-played",
    "user-library-read",
    "user-read-currently-playing",
    "user-read-playback-state",
]

# Global variable to store the authorization code
auth_code = None
auth_code_received = threading.Event()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        auth_code = self.path.split("code=")[1].split("&")[0]
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Authorization successful! You can close this window.")
        auth_code_received.set()
        threading.Thread(target=self.server.shutdown).start()


def start_local_server():
    httpd = HTTPServer(("localhost", 8000), CallbackHandler)
    httpd.serve_forever()


def get_refresh_token():
    # Start local server
    server_thread = threading.Thread(target=start_local_server)
    server_thread.start()

    # Construct authorization URL
    auth_params = urlencode(
        {"client_id": CLIENT_ID, "response_type": "code", "scope": " ".join(SCOPES)}
    )
    auth_url = f"https://accounts.spotify.com/authorize?{auth_params}&redirect_uri={quote(REDIRECT_URI)}"

    # Open browser for user authorization
    webbrowser.open(auth_url)

    # Wait for the authorization code with a timeout
    if not auth_code_received.wait(timeout=120):  # Wait for 2 minutes
        logger.error("Timeout waiting for authorization code")
        return None

    logger.info(f"Authorization code received: {auth_code}")

    # Exchange authorization code for tokens
    token_url = "https://accounts.spotify.com/api/token"
    auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": REDIRECT_URI,
    }

    try:
        response = requests.post(token_url, headers=headers, data=data)
        response.raise_for_status()
        tokens = response.json()

        if "refresh_token" in tokens:
            return tokens["refresh_token"]
        else:
            logger.error("No refresh token in response")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error exchanging authorization code for tokens: {e}")
        return None


if __name__ == "__main__":
    try:
        # Check if running in GitHub Actions
        if os.environ.get("GITHUB_ACTIONS"):
            # Skip the browser-based auth flow and just refresh the token
            token_url = "https://accounts.spotify.com/api/token"
            auth_header = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            headers = {
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            data = {
                "grant_type": "refresh_token",
                "refresh_token": os.getenv("REFRESH_TOKEN"),
            }

            response = requests.post(token_url, headers=headers, data=data)
            response.raise_for_status()
            tokens = response.json()
            
            if "refresh_token" in tokens:
                refresh_token = tokens["refresh_token"]
                logger.info("Successfully refreshed token")
            else:
                logger.info("No new refresh token provided, using existing one")
                refresh_token = os.getenv("REFRESH_TOKEN")
        else:
            # Regular browser-based flow for local development
            refresh_token = get_refresh_token()

        if refresh_token:
            # Update .env file with the token
            with open(".env", "r") as file:
                env_contents = file.read()

            if "REFRESH_TOKEN" in env_contents:
                env_contents = env_contents.replace(
                    f"REFRESH_TOKEN=\"{os.getenv('REFRESH_TOKEN')}\"",
                    f'REFRESH_TOKEN="{refresh_token}"',
                )
            else:
                env_contents += f'\nREFRESH_TOKEN="{refresh_token}"'

            with open(".env", "w") as file:
                file.write(env_contents)

            logger.info("Refresh token has been updated in the .env file.")
        else:
            logger.error("Failed to obtain refresh token")
            sys.exit(1)
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        sys.exit(1)
    finally:
        # Only force exit if not in GitHub Actions
        if not os.environ.get("GITHUB_ACTIONS"):
            os._exit(0)
