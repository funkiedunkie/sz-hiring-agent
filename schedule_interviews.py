"""Process availability replies and book ClubReady time blocks."""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from notifications.scheduling import process_scheduling_replies

count = process_scheduling_replies()
print(f"Scheduling run complete: {count} candidate(s) processed")
