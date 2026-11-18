"""YouTube Data API v3 integration: OAuth2 channel authorization and direct YouTube Shorts upload."""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Callable

logger = logging.getLogger("clipit.youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

SECRETS_FILE = Path("client_secrets.json")
TOKEN_FILE = Path(".youtube_token.json")


def is_configured() -> bool:
    """Check if client_secrets.json exists."""
    return SECRETS_FILE.exists()


def is_connected() -> bool:
    """Check if an authorized YouTube token exists and can be refreshed."""
    if not TOKEN_FILE.exists():
        return False
    try:
        creds = get_credentials()
        return creds is not None and creds.valid
    except Exception:
        return False


def get_credentials():
    """Load or refresh Google OAuth2 user credentials."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    if not TOKEN_FILE.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed credentials
            with open(TOKEN_FILE, "w", encoding="utf-8") as token:
                token.write(creds.to_json())
        return creds if creds and creds.valid else None
    except Exception as e:
        logger.error(f"Error loading credentials: {e}")
        return None


def authenticate_channel(open_browser: bool = True) -> Dict[str, Any]:
    """Run local OAuth authorization flow to link YouTube channel."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not SECRETS_FILE.exists():
        return {"success": False, "error": "client_secrets.json not found in project root."}

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            str(SECRETS_FILE),
            scopes=SCOPES,
        )
        # Runs a local lightweight web server to handle redirect automatically
        creds = flow.run_local_server(
            port=0,
            open_browser=open_browser,
            prompt="consent",
            authorization_prompt_message="Please visit this URL to authorize ClipIt to upload to your YouTube channel: {url}",
            success_message="Authentication successful! You can now close this tab and return to ClipIt Studio.",
        )

        # Save credentials to .youtube_token.json
        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

        channel = get_channel_info()
        return {"success": True, "channel": channel}
    except Exception as e:
        logger.exception("Failed to authenticate YouTube channel")
        return {"success": False, "error": str(e)}


def disconnect_channel() -> bool:
    """Remove authorized credentials file."""
    if TOKEN_FILE.exists():
        try:
            TOKEN_FILE.unlink()
            return True
        except Exception:
            pass
    return False


def get_channel_info() -> Optional[Dict[str, Any]]:
    """Retrieve connected YouTube channel details (title, handle, thumbnail)."""
    creds = get_credentials()
    if not creds:
        return None

    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", credentials=creds)
        res = youtube.channels().list(mine=True, part="snippet,statistics").execute()
        items = res.get("items", [])
        if not items:
            return None

        ch = items[0]
        snippet = ch.get("snippet", {})
        stats = ch.get("statistics", {})

        return {
            "id": ch.get("id"),
            "title": snippet.get("title", "YouTube Channel"),
            "customUrl": snippet.get("customUrl", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
            "subscribers": stats.get("subscriberCount", "0"),
            "videoCount": stats.get("videoCount", "0"),
        }
    except Exception as e:
        logger.error(f"Failed to fetch YouTube channel info: {e}")
        return None


def upload_short(
    file_path: Path,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    privacy_status: str = "public",
    progress_callback: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """Upload a vertical video as a YouTube Short using resumable upload."""
    if not file_path.exists():
        return {"success": False, "error": f"Video file not found: {file_path}"}

    creds = get_credentials()
    if not creds:
        return {"success": False, "error": "Not authenticated with YouTube. Please link your channel first."}

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        youtube = build("youtube", "v3", credentials=creds)

        # Truncate title to 100 characters per YouTube API requirements
        yt_title = title.strip()[:100]
        if "#Shorts" not in yt_title and len(yt_title) <= 92:
            yt_title = f"{yt_title} #Shorts"

        body = {
            "snippet": {
                "title": yt_title,
                "description": description,
                "tags": tags or ["#Shorts", "#Gaming"],
                "categoryId": "20",  # 20 = Gaming
            },
            "status": {
                "privacyStatus": privacy_status,  # "public", "unlisted", "private"
                "selfDeclaredMadeForKids": False,
            },
        }

        # 4MB chunk size for reliable resumable upload
        media = MediaFileUpload(
            str(file_path),
            chunksize=4 * 1024 * 1024,
            resumable=True,
            mimetype="video/mp4",
        )

        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and progress_callback:
                progress_callback(status.progress())

        video_id = response.get("id")
        return {
            "success": True,
            "video_id": video_id,
            "url": f"https://youtube.com/shorts/{video_id}",
            "title": yt_title,
            "privacy": privacy_status,
        }
    except Exception as e:
        logger.exception("YouTube upload failed")
        return {"success": False, "error": str(e)}
