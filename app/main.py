import base64
import os
import zoneinfo
from datetime import datetime
import logging
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request
from flask_caching import Cache

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load base64 images
base64_dir = Path(__file__).parent.parent / "base64"
with open(base64_dir / "placeholder_image.txt", "rb") as f_placeholder, \
     open(base64_dir / "spotify_logo.txt", "rb") as f_logo:
    B64_PLACEHOLDER_IMAGE = f_placeholder.read().decode("ascii")
    B64_SPOTIFY_LOGO = f_logo.read().decode("ascii")

SPOTIFY_API_BASE = "https://api.spotify.com/v1"

app = Flask(__name__, template_folder=str(Path(__file__).parent.parent / "templates"))
app.config['CACHE_TYPE'] = os.environ.get('CACHE_TYPE', 'simple')
app.config['CACHE_DEFAULT_TIMEOUT'] = int(os.environ.get('CACHE_DEFAULT_TIMEOUT', 60))

cache = Cache(app)

class SpotifyAPI:
    def __init__(self):
        self.session = requests.Session()
        self.token = self.refresh_token()

    def refresh_token(self):
        response = self.session.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": os.getenv("REFRESH_TOKEN"),
                "client_id": os.getenv("CLIENT_ID"),
                "client_secret": os.getenv("CLIENT_SECRET"),
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def request(self, endpoint):
        if endpoint.startswith("me/player/currently-playing"):
            return self._request_with_cache(endpoint, timeout=60)  # Cache for 1 minute
        elif endpoint.startswith("me/player/recently-played"):
            return self._request_with_cache(endpoint, timeout=300)  # Cache for 5 minutes
        else:
            return self._request_with_cache(endpoint, timeout=3600)  # Cache for 1 hour

    @cache.memoize()
    def _request_with_cache(self, endpoint, timeout):
        headers = {"Authorization": f"Bearer {self.token}"}
        response = self.session.get(f"{SPOTIFY_API_BASE}/{endpoint}", headers=headers)
        if response.status_code == 401:
            self.token = self.refresh_token()
            headers["Authorization"] = f"Bearer {self.token}"
            response = self.session.get(f"{SPOTIFY_API_BASE}/{endpoint}", headers=headers)
        response.raise_for_status()
        return response.json() if response.status_code != 204 else None

spotify_api = SpotifyAPI()

def fetch_current_track():
    data = spotify_api.request("me/player/currently-playing")
    if not data:
        recently_played = spotify_api.request("me/player/recently-played?limit=1")
        return recently_played["items"][0]["track"] if recently_played["items"] else None
    return data["item"]

def get_time_info():
    la_tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(la_tz)
    rounded = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    clock_emojis = "🕛🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚"
    half_hour_emojis = "🕧🕜🕝🕞🕟🕠🕡🕢🕣🕤🕥🕦"
    adjusted_hour = rounded.hour % 12
    emoji = clock_emojis[adjusted_hour] if rounded.minute == 0 else half_hour_emojis[adjusted_hour]
    formatted_time = rounded.strftime("%I:%M %p").lstrip("0")
    return emoji, formatted_time

@app.route("/")
@app.route("/svg")
def get_svg():
    track_data = get_current_track()
    if track_data and track_data["svg"]:
        response = Response(track_data["svg"], mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
        logger.info(f"Served current track SVG: {track_data['song']} by {track_data['artist']}")
        return response
    logger.error("Current track SVG not ready")
    return jsonify({"error": "SVG not ready"}), 503

@app.route("/link")
def get_track_link():
    track_data = get_current_track()
    if track_data and track_data["link"]:
        logger.info(f"Redirected to track link: {track_data['link']}")
        return redirect(track_data["link"])
    logger.error("No track link available")
    return jsonify({"error": "No track link available"}), 404

@app.route("/daylist")
@app.route("/daylist/light")
@app.route("/daylist/dark")
def daylist():
    daylist_phrase = get_daylist_phrase()
    color_scheme = "dark" if request.path.endswith("/dark") else "light"
    svg = render_template("daylist.svg", daylist_phrase=daylist_phrase, color_scheme=color_scheme, logo=B64_SPOTIFY_LOGO)
    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=1800, s-maxage=1800"
    logger.info(f"Served daylist SVG: {daylist_phrase}")
    return response

@app.route('/favicon.png')
@app.route('/favicon.ico')
def favicon():
    return Response(status=204)

@cache.memoize(timeout=3600)
def fetch_daylist_playlist():
    try:
        playlists = (playlist for offset in range(0, 1000, 50) for playlist in spotify_api.request(f"me/playlists?limit=50&offset={offset}")["items"])
        daylist = next((playlist for playlist in playlists if playlist["name"].lower().startswith("daylist")), None)
        if daylist:
            cleaned_name = daylist["name"].split("• ", 1)[-1] if "• " in daylist["name"] else daylist["name"]
            time_emoji, formatted_time = get_time_info()
            result = f"(It's around {formatted_time} {time_emoji}, another {cleaned_name})"
            logger.info(f"Fetched daylist: {result}")
            return result
        logger.warning("No daylist found in user playlists")
    except Exception as e:
        logger.error(f"Error fetching daylist: {str(e)}")
    return None

@cache.memoize(timeout=60)
def get_current_track():
    current_track = fetch_current_track()
    if current_track:
        image_url = current_track["album"]["images"][1]["url"] if current_track["album"]["images"] else None
        image_data = B64_PLACEHOLDER_IMAGE if not image_url else requests.get(image_url).content
        artist = current_track["artists"][0]["name"].replace("&", "&amp;")
        song = current_track["name"].replace("&", "&amp;")
        track_data = {
            "svg": render_template("recent.html", artist=artist, song=song, image=base64.b64encode(image_data).decode("ascii"), logo=B64_SPOTIFY_LOGO),
            "link": current_track["external_urls"]["spotify"],
            "artist": artist,
            "song": song
        }
        logger.info(f"Fetched new track: {song} by {artist}")
        return track_data
    logger.warning("No current track found")
    return None

@cache.memoize(timeout=1800)
def get_daylist_phrase():
    time_emoji, formatted_time = get_time_info()
    
    try:
        playlists = (playlist for offset in range(0, 1000, 50) for playlist in spotify_api.request(f"me/playlists?limit=50&offset={offset}")["items"])
        daylist = next((playlist for playlist in playlists if playlist["name"].lower().startswith("daylist")), None)
        if daylist:
            cleaned_name = daylist["name"].split("• ", 1)[-1] if "• " in daylist["name"] else daylist["name"]
            result = f"(It's around {formatted_time} {time_emoji}, another {cleaned_name})"
            logger.info(f"Fetched daylist: {result}")
            return result
        logger.warning("No daylist found in user playlists")
    except Exception as e:
        logger.error(f"Error fetching daylist: {str(e)}")
    
    # If daylist fetch fails, return a default message with the correct time
    return f"(It's around {formatted_time} {time_emoji})"

if __name__ == "__main__":
    app.run(debug=True)
