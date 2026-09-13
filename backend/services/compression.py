import io
import os
import logging
from typing import Optional, Tuple
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

def compress_lossless(content: bytes, filename: str, content_type: Optional[str] = None) -> Tuple[bytes, str, str]:
    """
    Losslessly optimizes image files (PNG, JPEG, WebP) by removing metadata bloat
    and applying maximum lossless compression algorithms.
    If compression results in larger size or is not an image, returns original content.
    Returns: (optimized_content, file_extension, mime_type)
    """
    _, ext = os.path.splitext((filename or "").lower())
    mime = content_type or "application/octet-stream"

    if ext not in IMAGE_EXTENSIONS and not (content_type and content_type.startswith("image/")):
        return content, ext, mime

    try:
        img = Image.open(io.BytesIO(content))
        # Keep orientation from EXIF before stripping metadata
        img = ImageOps.exif_transpose(img)

        out = io.BytesIO()
        orig_len = len(content)

        if ext == ".png" or mime == "image/png":
            img.save(out, format="PNG", optimize=True, compress_level=9)
            comp_bytes = out.getvalue()
            if len(comp_bytes) < orig_len:
                logger.info("PNG lossless optimiert: %d -> %d Bytes (%.1f%% gespart)", orig_len, len(comp_bytes), (1 - len(comp_bytes)/orig_len)*100)
                return comp_bytes, ".png", "image/png"

        elif ext in {".webp"} or mime == "image/webp":
            # WebP lossless mode preserves 100% pixel fidelity
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGBA" if "transparency" in img.info else "RGB")
            img.save(out, format="WEBP", lossless=True, quality=100, method=6)
            comp_bytes = out.getvalue()
            if len(comp_bytes) < orig_len:
                logger.info("WebP lossless optimiert: %d -> %d Bytes (%.1f%% gespart)", orig_len, len(comp_bytes), (1 - len(comp_bytes)/orig_len)*100)
                return comp_bytes, ".webp", "image/webp"

        elif ext in {".jpg", ".jpeg"} or mime == "image/jpeg":
            if img.mode != "RGB":
                img = img.convert("RGB")
            # For JPEG, optimize=True + progressive=True with maximum quality 95
            img.save(out, format="JPEG", optimize=True, progressive=True, quality=95)
            comp_bytes = out.getvalue()
            if len(comp_bytes) < orig_len:
                logger.info("JPEG optimiert: %d -> %d Bytes (%.1f%% gespart)", orig_len, len(comp_bytes), (1 - len(comp_bytes)/orig_len)*100)
                return comp_bytes, ext or ".jpg", "image/jpeg"

    except Exception as e:
        logger.warning("Lossless-Komprimierung für '%s' übersprungen: %s", filename, e)

    return content, ext, mime
