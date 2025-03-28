import base64
import json
import logging
import os
import sys
import time
import zipfile
import zoneinfo
from datetime import datetime
from io import BytesIO
from pathlib import Path
from random import uniform
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
DEFAULT_TIMEZONE = "Asia/Tokyo"

app = Flask(__name__, template_folder=str(Path(__file__).parent.parent / "templates"))
app.config["CACHE_TYPE"] = os.environ.get("CACHE_TYPE", "simple")
app.config["CACHE_DEFAULT_TIMEOUT"] = int(os.environ.get("CACHE_DEFAULT_TIMEOUT", 60))

cache = Cache(app)

# Near the top with other environment variables
VERCEL_COMMIT_SHA = os.getenv("VERCEL_GIT_COMMIT_SHA", "local")
PLAYWRIGHT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT", "45000"))  # 45 seconds default
SPOTIFY_COOKIES = os.getenv("SPOTIFY_COOKIES")


class SpotifyAPI:
    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self.token_expires = 0

        # Validate required environment variables
        required_vars = ["REFRESH_TOKEN", "CLIENT_ID", "CLIENT_SECRET"]
        missing_vars = [var for var in required_vars if not os.getenv(var)]

        if missing_vars:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing_vars)}"
            )

        self.refresh_token_str = os.getenv("REFRESH_TOKEN")
        self.token = self._refresh_token()

    def _refresh_token(self):
        """Refresh the access token and update expiration time."""
        try:
            response = self.session.post(
                "https://accounts.spotify.com/api/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token_str,
                    "client_id": os.getenv("CLIENT_ID"),
                    "client_secret": os.getenv("CLIENT_SECRET"),
                },
            )

            if response.status_code == 400:
                error_data = response.json()
                logger.error(
                    f"Token refresh failed: {error_data.get('error_description', 'Unknown error')}"
                )
                raise ValueError(
                    f"Token refresh failed: {error_data.get('error_description', 'Unknown error')}"
                )

            response.raise_for_status()
            data = response.json()
            self.token = data["access_token"]
            self.token_expires = time.time() + data.get("expires_in", 3600) - 60
            return self.token

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to refresh token: {str(e)}")
            raise

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

    def find_daylist(self, headless=True):
        """Find daylist using Playwright with cookie persistence."""
        cache_key = f"daylist_{datetime.now(zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)).strftime('%Y-%m-%d_%H')}"
        cookie_file = Path(__file__).parent / ".spotify_cookies.json"

        spotify_user = os.getenv("SPOTIFY_USER")
        spotify_pass = os.getenv("SPOTIFY_PASS")

        if not spotify_user or not spotify_pass:
            logger.error("Missing Spotify credentials")
            return None

        def setup_browser(playwright, headless):
            """Setup browser and context."""
            browser = playwright.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--window-size=1920,1080",
                    "--start-maximized",
                    "--enable-widevine",
                    "--no-sandbox",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/Los_Angeles",
            )

            # Try loading cookies from GitHub secrets first
            if SPOTIFY_COOKIES:
                try:
                    cookies = json.loads(SPOTIFY_COOKIES)
                    context.add_cookies(cookies)
                    logger.info("Loaded cookies from environment")
                except Exception as e:
                    logger.warning(f"Failed to load cookies from environment: {e}")

            # Fall back to local cookie file if available
            elif cookie_file.exists():
                try:
                    logger.info("Loading cookies from file")
                    context.add_cookies(json.loads(cookie_file.read_text()))
                except Exception as e:
                    logger.warning(f"Failed to load cookies from file: {e}")

            return browser, context

        def perform_login(page, cookie_file):
            """Handle Spotify login process."""
            try:
                logger.info("Logging in")
                page.get_by_test_id("login-button").click()
                page.wait_for_timeout(uniform(800, 1500))

                # Type credentials with natural delays
                for char in spotify_user:
                    page.type("#login-username", char, delay=uniform(50, 150))
                page.wait_for_timeout(uniform(500, 1000))

                for char in spotify_pass:
                    page.type("#login-password", char, delay=uniform(50, 150))
                page.wait_for_timeout(uniform(800, 1500))

                with page.expect_navigation(timeout=PLAYWRIGHT_TIMEOUT):
                    page.click("#login-button")

                page.wait_for_load_state("networkidle", timeout=PLAYWRIGHT_TIMEOUT)

                if "open.spotify.com" in page.url:
                    logger.info("Successfully logged in")
                    cookie_file.write_text(json.dumps(page.context.cookies()))
                    return True

                return False

            except Exception as e:
                logger.error(f"Login failed: {str(e)}")
                return False

        def handle_drm(page):
            """Handle DRM message if present."""
            try:
                if page.get_by_text(
                    "Playback of protected content is not enabled"
                ).is_visible():
                    logger.info("DRM message detected, performing hard refresh")
                    # Perform a hard refresh (Ctrl+Shift+R equivalent)
                    page.reload(
                        wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT
                    )
                    page.wait_for_timeout(uniform(800, 1500))
                    return True
                return False
            except Exception as e:
                logger.warning(f"DRM check failed: {e}")
                return False

        def navigate_to_search(page):
            """Navigate to daylist search page."""
            logger.info("Searching for daylist")
            try:
                page.goto(
                    "https://open.spotify.com/search/daylist",
                    wait_until="domcontentloaded",
                    timeout=PLAYWRIGHT_TIMEOUT,
                )
                page.wait_for_load_state("networkidle", timeout=PLAYWRIGHT_TIMEOUT)
                page.wait_for_timeout(uniform(800, 1500))
                handle_drm(page)
            except Exception as e:
                logger.warning(f"Navigation error: {e}")
                # Continue execution as login might fix the issue

        def extract_daylist_phrase(title):
            """Extract phrase from daylist title."""
            if "• " in title:
                phrase = title.split("• ", 1)[1]
                logger.info(f"Found daylist with bullet: {title}")
                cache.set(cache_key, phrase, timeout=1800)
                return phrase
            return None

        def find_daylist_element(page, require_bullet=False):
            """Find daylist element on page."""
            try:
                selector = (
                    'a[title^="daylist • "]'
                    if require_bullet
                    else 'a[title^="daylist"]'
                )
                element = page.wait_for_selector(selector, timeout=5000)
                return element
            except Exception:
                return None

        try:
            playwright = sync_playwright().start()
            browser = None
            result = None

            try:
                logger.info("Starting Playwright browser")
                browser, context = setup_browser(playwright, headless)
                page = context.new_page()
                logger.info("Browser page created")

                # Add page error handling with more detailed logging
                page.on(
                    "pageerror", lambda err: logger.error(f"Browser page error: {err}")
                )
                page.on(
                    "console",
                    lambda msg: (
                        logger.debug(f"Browser console {msg.type}: {msg.text}")
                        if msg.type == "error"
                        else None
                    ),
                )

                # Initial navigation with status
                logger.info("Navigating to Spotify search")
                navigate_to_search(page)

                # Handle login if needed with clear status
                if page.get_by_test_id("login-button").is_visible():
                    logger.info("Login required, attempting authentication")
                    if not perform_login(page, cookie_file):
                        logger.error("Login failed")
                        return None
                    logger.info("Login successful")

                # Look for daylist with status updates
                logger.info("Searching for daylist element")
                if element := find_daylist_element(page):
                    title = element.get_attribute("title")
                    logger.info(f"Found daylist element with title: {title}")
                    result = extract_daylist_phrase(title)

                    # If no bullet point found, try clicking into playlist
                    if not result:
                        logger.info(
                            "No bullet point found in title, attempting to regenerate"
                        )
                        element.click()
                        logger.info("Clicked daylist, waiting for page load")
                        page.wait_for_load_state(
                            "networkidle", timeout=PLAYWRIGHT_TIMEOUT
                        )
                        page.wait_for_timeout(uniform(800, 1500))

                        # Search again for bullet version
                        logger.info("Searching again for bullet version")
                        navigate_to_search(page)
                        if element := find_daylist_element(page, require_bullet=True):
                            title = element.get_attribute("title")
                            logger.info(f"Found bullet version with title: {title}")
                            result = extract_daylist_phrase(title)
                        else:
                            logger.error(
                                "Could not find bullet version after regeneration"
                            )
                else:
                    logger.error("Could not find daylist element on page")

                return result

            finally:
                if browser:
                    logger.info("Closing browser")
                    browser.close()
                playwright.stop()

        except Exception as e:
            logger.error(f"Critical error in find_daylist: {e}", exc_info=True)
            return None

    def get_cached_daylist(self, headless=True):
        """Get daylist from GitHub artifact if available."""
        cache_key = f"daylist_{datetime.now(zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)).strftime('%Y-%m-%d_%H')}"

        if cached := cache.get(cache_key):
            logger.info(f"Using memory-cached daylist phrase: {cached}")
            return cached

        try:
            logger.info("Fetching daylist from GitHub artifact")
            with requests.get(
                "https://nightly.link/jacksongoode/spotify-readme/workflows/update-daylist/main/daylist.zip",
                stream=True,
            ) as response:
                response.raise_for_status()

                with zipfile.ZipFile(BytesIO(response.content)) as zip_ref:
                    if txt_file := next(
                        (f for f in zip_ref.namelist() if f.endswith(".txt")), None
                    ):
                        phrase = zip_ref.read(txt_file).decode("utf-8").strip()
                        if phrase:
                            logger.info(f"Using artifact daylist phrase: {phrase}")
                            cache.set(cache_key, phrase, timeout=1800)
                            return phrase

        except Exception as e:
            logger.error(f"Error reading artifact daylist: {e}")

        return self.find_daylist(headless=headless)


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
    la_tz = zoneinfo.ZoneInfo(DEFAULT_TIMEZONE)
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
    now = datetime.now(zoneinfo.ZoneInfo(DEFAULT_TIMEZONE))
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
        phrase = spotify_api.get_cached_daylist(headless=False)
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
