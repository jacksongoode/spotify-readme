import os
import sys
from pathlib import Path

# Add app directory to path so we can import from it
sys.path.append(str(Path(__file__).parent.parent))

import logging

from app.main import SpotifyAPI

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


def check_env_vars():
    """Check if all required environment variables are present and non-empty."""
    required_vars = [
        "CLIENT_ID",
        "CLIENT_SECRET",
        "REFRESH_TOKEN",
        "SPOTIFY_USER",
        "SPOTIFY_PASS",
    ]

    for var in required_vars:
        value = os.getenv(var)
        # Log the first and last 4 chars of sensitive data
        if value:
            masked_value = f"{value[:4]}...{value[-4:]}" if len(value) > 8 else "***"
            logger.debug(f"{var} is present with value: {masked_value}")
        else:
            logger.error(f"{var} is missing or empty")
            return False
    return True


def main():
    logger.info("Starting daylist fetch process")

    # Check environment variables first
    if not check_env_vars():
        logger.error("Missing required environment variables")
        return 1

    # Create data directory if it doesn't exist
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)
    logger.debug(f"Created/verified data directory at {data_dir}")

    try:
        # Initialize API and fetch daylist
        logger.info("Initializing SpotifyAPI")
        api = SpotifyAPI()
        logger.info("Fetching daylist")
        daylist = api.find_daylist()

        if daylist:
            # Write daylist to file
            output_file = data_dir / "daylist.txt"
            logger.info(f"Writing daylist to {output_file}")
            with open(output_file, "w") as f:
                f.write(daylist)
            logger.info(f"Successfully saved daylist: {daylist}")
            return 0

        logger.error("Failed to fetch daylist")
        return 1

    except Exception as e:
        logger.error(f"Error during execution: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
