"""
config.py — Load environment variables from .env file.
All configuration values used by the app are defined here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Imou API ---
IMOU_APP_ID = os.getenv("IMOU_APP_ID", "")
IMOU_APP_SECRET = os.getenv("IMOU_APP_SECRET", "")
# Regional base URL — change to your region:
#   EU:   https://openapi-fk.easy4ip.com/openapi
#   US:   https://openapi-or.easy4ip.com/openapi
#   Asia: https://openapi-sg.easy4ip.com/openapi
IMOU_BASE_URL = os.getenv("IMOU_BASE_URL", "https://openapi.easy4ip.com/openapi")

# --- Flask ---
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-please")
SESSION_LIFETIME_HOURS = int(os.getenv("SESSION_LIFETIME_HOURS", "8"))
PORT = int(os.getenv("PORT", "5000"))
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

# --- Database ---
DB_PATH = os.getenv("DB_PATH", "/data/imou_portal.db")

# --- Alarm image cache directory (next to DB) ---
ALARM_IMG_DIR = os.path.join(os.path.dirname(DB_PATH), "alarm_images")

# --- Webhook ---
# Secret token Imou will send in webhook calls (optional, for verification)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# --- Admin ---
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
