import base64
import os
import zoneinfo
from datetime import datetime, timedelta
import logging

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request
from flask_caching import Cache
from flask_apscheduler import APScheduler

load_dotenv()
app = Flask(__name__)
app.config.from_mapping(
    CACHE_TYPE="simple",
    CACHE_DEFAULT_TIMEOUT=60,
)
cache = Cache(app)
cache.init_app(app)

# Set up logging
app.logger.setLevel(logging.INFO)

scheduler = APScheduler()
scheduler.init_app(app)
scheduler.start()

SPOTIFY_API_BASE = "https://api.spotify.com/v1"
B64_PLACEHOLDER_IMAGE, B64_SPOTIFY_LOGO = None, None

# Global variables to store the latest SVGs
latest_track_svg = None
latest_daylist_svg = None
current_track = None


def load_base64_images():
    global B64_PLACEHOLDER_IMAGE, B64_SPOTIFY_LOGO
    with open("base64/placeholder_image.txt", "rb") as f_placeholder, open(
        "base64/spotify_logo.txt", "rb"
    ) as f_logo:
        B64_PLACEHOLDER_IMAGE = f_placeholder.read().decode("ascii")
        B64_SPOTIFY_LOGO = f_logo.read().decode("ascii")


load_base64_images()


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
        response_data = response.json()

        if new_refresh_token := response_data.get("refresh_token"):
            os.environ["REFRESH_TOKEN"] = new_refresh_token

            if os.path.exists(".env"):
                with open(".env", "r") as env_file:
                    lines = env_file.readlines()

                with open(".env", "w") as env_file:
                    for line in lines:
                        if line.startswith("REFRESH_TOKEN"):
                            env_file.write(f'REFRESH_TOKEN="{new_refresh_token}"\n')
                        else:
                            env_file.write(line)

        return response.json()["access_token"]

    @cache.memoize()
    def request(self, endpoint):
        headers = {"Authorization": f"Bearer {self.token}"}
        response = self.session.get(f"{SPOTIFY_API_BASE}/{endpoint}", headers=headers)

        if response.status_code == 401:  # Token expired
            self.refresh_token()
            headers["Authorization"] = f"Bearer {self.token}"
            response = self.session.get(
                f"{SPOTIFY_API_BASE}/{endpoint}", headers=headers
            )

        if response.status_code == 204:  # No content
            return None

        response.raise_for_status()
        return response.json()


spotify_api = SpotifyAPI()


def fetch_current_track():
    data = spotify_api.request("me/player/currently-playing")
    if not data:
        recently_played = spotify_api.request("me/player/recently-played?limit=1")
        return (
            recently_played["items"][0]["track"] if recently_played["items"] else None
        )
    return data["item"]


def get_current_user_playlists(limit=50, offset=0):
    results = spotify_api.request(f"me/playlists?limit={limit}&offset={offset}")
    playlists = results["items"]

    return playlists, results["next"] is not None


def get_time_info():
    la_tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(la_tz)
    rounded = now.replace(
        minute=30 if now.minute < 30 else 0, second=0, microsecond=0
    ) + timedelta(hours=0 if now.minute < 30 else 1)

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


@cache.memoize(timeout=86400)
def fetch_daylist_playlist():
    try:
        playlists = (
            playlist
            for offset in range(0, 1000, 50)
            for playlist in get_current_user_playlists(limit=50, offset=offset)[0]
        )
        daylist = next(
            (
                playlist
                for playlist in playlists
                if playlist["name"].lower().startswith("daylist")
            ),
            None,
        )

        if daylist:
            full_name = daylist["name"]
            cleaned_name = (
                full_name.split("• ", 1)[-1] if "• " in full_name else full_name
            )
            time_emoji, formatted_time = get_time_info()
            return f"(It's about {formatted_time} {time_emoji}, the mood is a {cleaned_name})"

        return None
    except Exception:
        return None


@cache.memoize(timeout=86400)
def fetch_and_cache_image(image_url):
    image_response = spotify_api.session.get(image_url)
    image_data = image_response.content
    return base64.b64encode(image_data).decode("ascii")


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f"An unexpected error occurred: {e}")
    return jsonify({"error": "An unexpected error occurred"}), 500


def update_track_svg():
    global latest_track_svg, current_track
    app.logger.info("Updating track SVG...")
    with app.app_context():
        current_track = fetch_current_track()

        if current_track:
            app.logger.info(
                f"Retrieved track: {current_track['name']} by {current_track['artists'][0]['name']}"
            )
            image_url = current_track["album"]["images"][1]["url"] if current_track["album"]["images"] else None
            image_data = B64_PLACEHOLDER_IMAGE if not image_url else fetch_and_cache_image(image_url)
            # Assemble SVG
            latest_track_svg = render_template(
                "recent.html",
                artist=current_track["artists"][0]["name"].replace("&", "&amp;"),
                song=current_track["name"].replace("&", "&amp;"),
                image=image_data,
                logo=B64_SPOTIFY_LOGO,
            )
        else:
            app.logger.info("No current track found")


def update_daylist_svg():
    global latest_daylist_svg
    app.logger.info("Updating daylist SVG...")
    with app.app_context():
        daylist_phrase = fetch_daylist_playlist()

        if daylist_phrase:
            app.logger.info(f"Retrieved daylist: {daylist_phrase}")
            latest_daylist_svg = daylist_phrase  # Store only the phrase
        else:
            app.logger.info("No daylist found")


# Schedule the playing track update every 60 seconds
@scheduler.task("interval", id="update_track_svg", seconds=60)
def scheduled_update_track_svg():
    app.logger.info("Running scheduled task: update_track_svg")
    update_track_svg()


# Schedule the daylist SVG update every 30 minutes
@scheduler.task("interval", id="update_daylist_svg", seconds=1800)
def scheduled_update_daylist_svg():
    app.logger.info("Running scheduled task: update_daylist_svg")
    update_daylist_svg()


@app.route("/")
@app.route("/svg")
def get_svg():
    if latest_track_svg:
        response = Response(latest_track_svg, mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "public, max-age=60"
        return response
    return jsonify({"error": "SVG not ready"}), 503


@app.route("/link")
def get_track_link():
    if current_track:
        return redirect(current_track["external_urls"]["spotify"])
    return jsonify({"error": "No track link available"}), 404


@app.route("/daylist")
@app.route("/daylist/light")
@app.route("/daylist/dark")
def daylist():
    global latest_daylist_svg
    if latest_daylist_svg:
        color_scheme = "dark" if request.path.endswith("/dark") else "light"
        
        # Generate a new SVG with the correct color scheme
        svg = render_template(
            "daylist.svg",
            daylist_phrase=latest_daylist_svg,
            color_scheme=color_scheme,
            logo=B64_SPOTIFY_LOGO,
        )
        
        response = Response(svg, mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "public, max-age=1800"  # Cache for 30 minutes
        return response
    return jsonify({"error": "Daylist SVG not ready"}), 503


@app.route('/favicon.ico')
def favicon():
    return Response(status=204)

# Move this outside of the if __name__ == "__main__" block
with app.app_context():
    # Run the jobs once immediately to populate the SVGs
    app.logger.info("Initial run of update_track_svg")
    update_track_svg()
    app.logger.info("Initial run of update_daylist_svg")
    update_daylist_svg()

app
