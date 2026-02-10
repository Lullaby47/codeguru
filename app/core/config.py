"""
Configuration constants for the application.
"""
import os
from pathlib import Path

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
except ImportError:
    # python-dotenv not installed, try to load from os.environ directly
    pass

# Main Admin User ID - IMMUTABLE CONSTANT
# This is the user ID of the main admin (root user).
# This constant MUST NOT be changed through UI or database logic.
# The user with this ID has full admin authority and can promote others to co-admin.
# This is the ONLY source of truth for main admin identity.
MAIN_ADMIN_USER_ID = 1

# OpenAI API Key for mentor hints
# IMPORTANT: Do NOT hardcode keys in code or commit them to git.
# Set OPENAI_API_KEY in your environment (or hosting provider env vars).
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

