import base64
import logging
import os
import sys
import time
import zoneinfo
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request
from flask_caching import Cache
from markupsafe import Markup
from playwright.sync_api import sync_playwright

# Load environment variables
load_dotenv()

# Configure logging to write to stdout
logging.basicConfig(
    stream=sys.stdout,  # Write to stdout for Vercel
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Load base64 images
base64_dir = Path(__file__).parent.parent / "base64"
with open(base64_dir / "placeholder_image.txt", "rb") as f_placeholder, open(
    base64_dir / "spotify_logo.txt", "rb"
) as f_logo:
    B64_PLACEHOLDER_IMAGE = f_placeholder.read().decode("ascii")
    B64_SPOTIFY_LOGO = f_logo.read().decode("ascii")

SPOTIFY_API_BASE = "https://api.spotify.com/v1"

app = Flask(__name__, template_folder=str(Path(__file__).parent.parent / "templates"))
app.config["CACHE_TYPE"] = os.environ.get("CACHE_TYPE", "simple")
app.config["CACHE_DEFAULT_TIMEOUT"] = int(os.environ.get("CACHE_DEFAULT_TIMEOUT", 60))

cache = Cache(app)

# Near the top with other environment variables
VERCEL_COMMIT_SHA = os.getenv("VERCEL_GIT_COMMIT_SHA", "local")


class SpotifyAPI:
    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self.token_expires = 0
        self.refresh_token_str = os.getenv("REFRESH_TOKEN")
        self.token = self._refresh_token()

    def _refresh_token(self):
        """Refresh the access token and update expiration time."""
        response = self.session.post(
            "https://accounts.spotify.com/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token_str,
                "client_id": os.getenv("CLIENT_ID"),
                "client_secret": os.getenv("CLIENT_SECRET"),
            },
        )
        response.raise_for_status()
        data = response.json()
        self.token = data["access_token"]
        self.token_expires = time.time() + data.get("expires_in", 3600) - 60
        return self.token

    def request(self, endpoint):
        """Request with endpoint-specific caching."""
        if endpoint.startswith("me/player/currently-playing"):
            return self._request_with_cache(endpoint)  # Default 1 min cache
        elif endpoint.startswith("me/player/recently-played"):
            return self._request_with_cache(endpoint, timeout=300)  # 5 min cache
        else:
            return self._request_no_cache(endpoint)  # No cache for other endpoints

    def _request_no_cache(self, endpoint):
        """Make uncached request."""
        # Check token expiration
        if time.time() > self.token_expires:
            self._refresh_token()

        headers = {"Authorization": f"Bearer {self.token}"}
        response = self.session.get(f"{SPOTIFY_API_BASE}/{endpoint}", headers=headers)

        # Handle 401 as backup (shouldn't normally happen with expiration check)
        if response.status_code == 401:
            self._refresh_token()
            headers["Authorization"] = f"Bearer {self.token}"
            response = self.session.get(
                f"{SPOTIFY_API_BASE}/{endpoint}", headers=headers
            )

        response.raise_for_status()
        return response.json() if response.status_code != 204 else None

    @cache.memoize(timeout=60)  # Default 1 min cache
    def _request_with_cache(self, endpoint, timeout=60):
        """Make cached request."""
        return self._request_no_cache(endpoint)

    def find_daylist(self):
        """Find daylist using Playwright with UI login."""
        cache_key = f"daylist_{datetime.now(zoneinfo.ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d_%H')}"

        # Check cache first
        if cached := cache.get(cache_key):
            logger.info(f"Using cached daylist phrase: {cached}")
            return cached

        spotify_user = os.getenv("SPOTIFY_USER")
        spotify_pass = os.getenv("SPOTIFY_PASS")

        if not spotify_user or not spotify_pass:
            logger.error("Missing Spotify credentials")
            return None

        try:
            logger.info("Fetching daylist via browser")
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled"],
                )

                page = browser.new_context().new_page()
                page.set_default_timeout(30000)  # 30 seconds

                # Login
                logger.info("Logging in...")
                page.goto(
                    "https://accounts.spotify.com/login", wait_until="networkidle"
                )
                page.fill("#login-username", spotify_user)
                page.fill("#login-password", spotify_pass)

                with page.expect_navigation():
                    page.click("#login-button")

                # Navigate to web player
                logger.info("Opening web player...")
                page.click("button[data-testid='web-player-link']")
                page.wait_for_load_state("networkidle")

                # Search for daylist (twice to handle DRM)
                logger.info("Searching for daylist...")
                for _ in range(2):
                    page.goto(
                        "https://open.spotify.com/search/daylist",
                        wait_until="networkidle",
                    )
                    page.wait_for_timeout(500)

                # Find daylist
                element = page.wait_for_selector('a[title^="daylist • "]')
                if element:
                    title = element.get_attribute("title")
                    if "• " in title:
                        phrase = title.split("• ", 1)[1]
                        logger.info(f"Found daylist: {title}")
                        cache.set(
                            cache_key, phrase, timeout=1800
                        )  # Cache for 30 minutes
                        return phrase

        except Exception as e:
            logger.error(f"Error finding daylist: {e}")
            return None

        logger.warning("No daylist found")
        cache.set(cache_key, None, timeout=1800)  # Cache negative result
        return None

    def get_cached_daylist(self):
        """Get daylist from GitHub artifact if available."""
        cache_key = f"daylist_{datetime.now(zoneinfo.ZoneInfo('America/Los_Angeles')).strftime('%Y-%m-%d_%H')}"

        # Check memory cache first
        if cached := cache.get(cache_key):
            logger.info(f"Using memory-cached daylist phrase: {cached}")
            return cached

        try:
            # Try to read from artifact file
            data_file = Path(__file__).parent.parent / "data" / "daylist.txt"
            if data_file.exists():
                phrase = data_file.read_text().strip()
                if phrase:
                    logger.info(f"Using file-cached daylist phrase: {phrase}")
                    cache.set(cache_key, phrase, timeout=1800)
                    return phrase
        except Exception as e:
            logger.error(f"Error reading cached daylist: {e}")

        # Fallback to browser automation if needed
        return self.find_daylist()


spotify_api = SpotifyAPI()


def fetch_current_track():
    data = spotify_api.request("me/player/currently-playing")
    if not data:
        recently_played = spotify_api.request("me/player/recently-played?limit=1")
        return (
            recently_played["items"][0]["track"] if recently_played["items"] else None
        )
    return data["item"]


def get_time_info():
    la_tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(la_tz)
    rounded = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    clock_emojis = "🕛🕐🕑🕒🕓🕔🕕🕖🕗🕘🕙🕚"
    half_hour_emojis = "🕧🕜🕝🕞🕟🕠🕡🕢🕣🕤🕥🕦"
    adjusted_hour = rounded.hour % 12
    emoji = (
        clock_emojis[adjusted_hour]
        if rounded.minute == 0
        else half_hour_emojis[adjusted_hour]
    )
    formatted_time = rounded.strftime("%I:%M %p").lstrip("0")
    return emoji, formatted_time


def get_time_of_day_phrase():
    """Return appropriate greeting based on time of day in LA."""
    now = datetime.now(zoneinfo.ZoneInfo("America/Los_Angeles"))
    hour = now.hour

    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    else:
        return "evening"


@app.route("/")
@app.route("/svg")
def get_svg():
    track_data = get_current_track()
    if track_data and track_data["svg"]:
        response = Response(track_data["svg"], mimetype="image/svg+xml")
        response.headers["Cache-Control"] = (
            "public, max-age=60, s-maxage=60, must-revalidate"
        )
        response.headers["ETag"] = f'W/"{VERCEL_COMMIT_SHA}"'
        logger.info(
            f"Served current track SVG: {track_data['song']} by {track_data['artist']}"
        )
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
    time_emoji, formatted_time = get_time_info()

    try:
        phrase = spotify_api.get_cached_daylist()
        time_of_day = get_time_of_day_phrase()

        # Set default phrase
        daylist_phrase = f"(It's around {formatted_time} {time_emoji}, another {time_of_day} of music)"
        cache_duration = 60

        # Only update phrase if we got a valid one from find_daylist
        if phrase:
            logger.info(f"Found daylist with phrase: '{phrase}'")
            daylist_phrase = (
                f"(It's around {formatted_time} {time_emoji}, another {phrase})"
            )
            cache_duration = 1800
        else:
            logger.warning("No valid daylist found")

        svg = render_template(
            "daylist.svg",
            daylist_phrase=daylist_phrase,
            color_scheme="dark" if request.path.endswith("/dark") else "light",
            logo=B64_SPOTIFY_LOGO,
        )

        response = Response(svg, mimetype="image/svg+xml")
        response.headers["Cache-Control"] = (
            f"public, max-age={cache_duration}, s-maxage={cache_duration}, must-revalidate"
        )
        response.headers["ETag"] = f'W/"{VERCEL_COMMIT_SHA}"'
        logger.info(f"Served daylist SVG: {daylist_phrase}")
        return response

    except Exception as e:
        logger.error(f"Error in daylist route: {str(e)}")
        return Response(status=500)


@app.route("/favicon.png")
@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


@cache.memoize(timeout=60)
def get_current_track():
    current_track = fetch_current_track()
    if current_track:
        image_url = (
            current_track["album"]["images"][1]["url"]
            if current_track["album"]["images"]
            else None
        )
        image_data = (
            B64_PLACEHOLDER_IMAGE if not image_url else requests.get(image_url).content
        )

        # First escape XML entities, then wrap in Markup to prevent double-escaping
        artist = Markup(xml_escape(current_track["artists"][0]["name"]))
        song = Markup(xml_escape(current_track["name"]))

        track_data = {
            "svg": render_template(
                "recent.html",
                artist=artist,
                song=song,
                image=base64.b64encode(image_data).decode("ascii"),
                logo=B64_SPOTIFY_LOGO,
            ),
            "link": current_track["external_urls"]["spotify"],
            "artist": artist,
            "song": song,
        }
        logger.info(f"Fetched new track: {song} by {artist}")
        return track_data
    logger.warning("No current track found")
    return None


if __name__ == "__main__":
    # Check if we're in development or production
    is_development = os.environ.get("FLASK_ENV") == "development"

    # Only enable debug mode in development
    app.run(debug=is_development)
