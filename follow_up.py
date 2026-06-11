"""Cron entry point: follow-ups, auto-archive, and manager reply notifications."""
import logging
from notifications.follow_up import run_follow_ups, run_auto_archive, run_reply_notifications

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    followup_count = run_follow_ups()
    archive_counts = run_auto_archive()
    notify_count = run_reply_notifications()
    print(
        f"Follow-ups sent: {followup_count} | "
        f"Auto-archived: {archive_counts['dq']} DQ'd, {archive_counts['unresponsive']} unresponsive | "
        f"Manager notifications: {notify_count}"
    )
