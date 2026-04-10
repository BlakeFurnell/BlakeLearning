"""
config.py

Loads environment variables from .env using python-dotenv and exposes them
as a Config class consumed by the Flask app and service modules.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Central configuration sourced from environment variables."""

    SAM_API_KEY: str = os.getenv("SAM_API_KEY", "")
    OLLAMA_API_KEY: str = os.getenv("OLLAMA_API_KEY", "")
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "https://ollama.com/api")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma4:31b")

    # Flask settings
    DEBUG: bool = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me-in-production")
