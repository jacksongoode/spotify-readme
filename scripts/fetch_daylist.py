import os
import sys
from pathlib import Path

# Add app directory to path so we can import from it
sys.path.append(str(Path(__file__).parent.parent))

from app.main import SpotifyAPI


def main():
    # Create data directory if it doesn't exist
    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(exist_ok=True)

    # Initialize API and fetch daylist
    api = SpotifyAPI()
    daylist = api.find_daylist()

    if daylist:
        # Write daylist to file
        with open(data_dir / "daylist.txt", "w") as f:
            f.write(daylist)
        print(f"Successfully saved daylist: {daylist}")
        return 0

    print("Failed to fetch daylist")
    return 1


if __name__ == "__main__":
    sys.exit(main())
