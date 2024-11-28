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


def main():
    logger.info("Starting daylist fetch process")

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
