"""Minimal Gemini REST client for reading the weekly meal-plan photo into a
day-by-day table. Uses plain httpx instead of the google-generativeai SDK to
avoid an extra dependency for what's a single API call."""
from backend.config import settings
from datetime import date
from PIL import Image, ImageOps
import httpx, json, logging, base64, io, re

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
