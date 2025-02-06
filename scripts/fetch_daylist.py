import argparse
import logging
import os
import sys
from pathlib import Path

# Add app directory to path so we can import from it
sys.path.append(str(Path(__file__).parent.parent))

from app.main import SpotifyAPI

log_format = "%(asctime)s - %(levelname)s - %(message)s"
if os.environ.get("GITHUB_ACTIONS"):
    log_format = (
        "::%(levelname)s:: %(message)s"
    )

logging.basicConfig(level=logging.INFO, format=log_format, stream=sys.stdout)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--headed", action="store_true", help="Run in headed mode")
    args = parser.parse_args()

    logger.info("Starting daylist fetch process")

    # Create data directory if it doesn't exist
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    try:
        # Initialize API and fetch daylist
        logger.info("Initializing SpotifyAPI")
        api = SpotifyAPI()

        logger.info("Starting browser session")
        daylist = api.find_daylist(headless=(not args.headed))

        if daylist:
            # Write daylist to file
            output_file = data_dir / "daylist.txt"
            logger.info(f"Writing daylist to {output_file}")
            with open(output_file, "w") as f:
                f.write(daylist)
            logger.info(f"Successfully saved daylist: {daylist}")
            return 0

        logger.error("Failed to fetch daylist - no valid daylist found")
        return 1

    except Exception as e:
        logger.error(f"Error during execution: {str(e)}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
