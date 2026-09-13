import os
import re
import socket
import logging
from typing import Optional
from fastapi import HTTPException

logger = logging.getLogger(__name__)

EICAR_PATTERN = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

# Disallowed dangerous executable extensions
DANGEROUS_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".sh", ".bash", ".vbs", ".vbe", ".js", ".jse",
    ".wsf", ".wsh", ".msc", ".scr", ".pif", ".application", ".gadget",
    ".ps1", ".ps1xml", ".ps2", ".ps2xml", ".psc1", ".psc2", ".reg",
    ".dll", ".so", ".dylib", ".sys", ".drv", ".cpl", ".ocx", ".com"
}

# Suspicious payload signatures
SUSPICIOUS_SIGNATURES = [
    re.compile(b"<script[\\s>]", re.IGNORECASE),
    re.compile(b"javascript:", re.IGNORECASE),
    re.compile(b"<\\?php", re.IGNORECASE),
    re.compile(b"eval\\s*\\(\\s*\\$_", re.IGNORECASE),
    re.compile(b"base64_decode\\s*\\(", re.IGNORECASE),
    re.compile(b"system\\s*\\(\\s*\\$_", re.IGNORECASE),
    re.compile(b"shell_exec\\s*\\(", re.IGNORECASE),
    re.compile(b"passthru\\s*\\(", re.IGNORECASE),
    re.compile(b"/bin/sh\\b", re.IGNORECASE),
    re.compile(b"/bin/bash\\b", re.IGNORECASE),
    re.compile(b"powershell\\.exe", re.IGNORECASE),
    re.compile(b"cmd\\.exe", re.IGNORECASE),
]

def _check_clamav_stream(content: bytes, host: str = "127.0.0.1", port: int = 3310) -> Optional[str]:
    """Optional scan via clamd TCP INSTREAM protocol if ClamAV daemon is running."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect((host, port))
        s.sendall(b"zINSTREAM\0")

        # Send in 2048-byte chunks (format: 4-byte big-endian size + chunk)
        chunk_size = 2048
        for i in range(0, len(content), chunk_size):
            chunk = content[i : i + chunk_size]
            s.sendall(len(chunk).to_bytes(4, byteorder="big") + chunk)
        s.sendall((0).to_bytes(4, byteorder="big"))  # Terminate stream

        response = b""
        while True:
            data = s.recv(1024)
            if not data:
                break
            response += data
        s.close()

        resp_str = response.decode("utf-8", errors="ignore").strip()
        if "FOUND" in resp_str:
            return resp_str
        return None
    except Exception:
        # ClamAV daemon not reachable; fallback to built-in heuristic scanner
        return None


async def scan_file(content: bytes, filename: str, content_type: Optional[str] = None) -> None:
    """
    Scans an uploaded file for malware, virus signatures, disguised binaries,
    and malicious script injections. Raises HTTPException(400) if suspicious or dangerous.
    """
    if not content:
        raise HTTPException(400, "Die hochgeladene Datei ist leer.")

    fname_lower = (filename or "").lower().strip()

    # 1. Check double extensions (e.g. "photo.jpg.exe", "notes.pdf.bat")
    parts = fname_lower.split(".")
    if len(parts) > 2:
        for ext_part in parts[1:]:
            ext_dot = f".{ext_part}"
            if ext_dot in DANGEROUS_EXTENSIONS:
                logger.warning("Virenscanner: Doppelte gefährliche Erweiterung blockiert: %s", filename)
                raise HTTPException(
                    400,
                    f"Sicherheitswarnung: Dateityp '{ext_dot}' ist aus Sicherheitsgründen nicht erlaubt."
                )

    # 2. Check primary extension
    _, ext = os.path.splitext(fname_lower)
    if ext in DANGEROUS_EXTENSIONS:
        logger.warning("Virenscanner: Ausführbare Datei blockiert: %s", filename)
        raise HTTPException(
            400,
            f"Sicherheitswarnung: Dateityp '{ext}' darf nicht hochgeladen werden."
        )

    # 3. Check for EICAR standard test signature
    if EICAR_PATTERN in content:
        logger.critical("Virenscanner: EICAR-Testvirus erkannt in Datei: %s", filename)
        raise HTTPException(
            400,
            "Sicherheitswarnung: EICAR-Test-Virus in der Datei erkannt! Upload abgebrochen."
        )

    # 4. Check magic bytes disguise:
    # If the extension looks like an image or document, but the file is actually a Windows PE or ELF executable
    safe_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx"}
    if ext in safe_exts:
        # Windows PE executable signature: 'MZ' at offset 0
        if len(content) >= 2 and content[:2] == b"MZ":
            logger.critical("Virenscanner: Getarnte Windows-Executable erkannt (.exe als %s): %s", ext, filename)
            raise HTTPException(
                400,
                "Sicherheitswarnung: Getarnte ausführbare Datei (Windows PE) erkannt. Upload verweigert."
            )
        # Linux ELF executable signature: 0x7F 'ELF'
        if len(content) >= 4 and content[:4] == b"\x7fELF":
            logger.critical("Virenscanner: Getarnte Linux-Executable erkannt: %s", filename)
            raise HTTPException(
                400,
                "Sicherheitswarnung: Getarnte Binärdatei (Linux ELF) erkannt. Upload verweigert."
            )

    # 5. Check SVG / HTML / Text for malicious script injections
    is_text_or_svg = (
        ext in {".svg", ".html", ".htm", ".xml", ".txt"} or
        (content_type and any(ct in content_type.lower() for ct in ["svg", "html", "xml", "text"]))
    )
    if is_text_or_svg:
        sample = content[:100000]  # Check first 100 KB
        for pat in SUSPICIOUS_SIGNATURES:
            if pat.search(sample):
                logger.warning("Virenscanner: Schädliches Skript-Muster '%s' gefunden in: %s", pat.pattern, filename)
                raise HTTPException(
                    400,
                    "Sicherheitswarnung: Die Datei enthält potenziell schädliche Skripte oder Code."
                )

    # 6. Try ClamAV daemon if running
    clamav_host = os.getenv("CLAMAV_HOST", "127.0.0.1")
    clamav_port = int(os.getenv("CLAMAV_PORT", "3310"))
    clam_result = _check_clamav_stream(content, host=clamav_host, port=clamav_port)
    if clam_result:
        logger.critical("Virenscanner ClamAV Treffer: %s in %s", clam_result, filename)
        raise HTTPException(
            400,
            f"Sicherheitswarnung (ClamAV): Schadsoftware erkannt ({clam_result}). Upload verweigert."
        )

    logger.debug("Virenscanner: Datei '%s' (%d Bytes) erfolgreich geprüft.", filename, len(content))
