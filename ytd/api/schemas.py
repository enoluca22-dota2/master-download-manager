"""Request bodies for the local API. Every field is validated; nothing here
allows passing an arbitrary command through, and save folders are checked
by ytd.files.validate_save_dir (absolute, local, no '..')."""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

_MAX_URL = 8192


def _clean_url(v):
    if not v or not v.strip():
        raise ValueError("url must not be blank")
    v = v.strip()
    if len(v) > _MAX_URL:
        raise ValueError("url is too long")
    if any(c in v for c in "\r\n\x00"):
        raise ValueError("url contains control characters")
    return v


class DetectRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def not_blank(cls, v):
        return _clean_url(v)


class DownloadOptions(BaseModel):
    """Optional media choices (all validated by the engine as well)."""
    container: Optional[str] = Field(default=None, pattern=r"^(mp4|mkv|original)$")
    prefer_h264: Optional[bool] = None
    subtitles_langs: Optional[List[str]] = None
    subtitles_auto: Optional[bool] = None
    subtitles_format: Optional[str] = Field(default=None, pattern=r"^(srt|vtt|ass)$")
    subtitles_embed: Optional[bool] = None
    embed_metadata: Optional[bool] = None
    embed_thumbnail: Optional[bool] = None
    playlist_items: Optional[str] = Field(default=None, pattern=r"^[0-9,:\- ]{1,200}$")
    speed_limit_kbps: Optional[int] = Field(default=None, ge=0, le=10_000_000)

    def to_engine_options(self):
        o = {}
        for key in ("container", "prefer_h264", "embed_metadata", "embed_thumbnail", "playlist_items", "speed_limit_kbps"):
            value = getattr(self, key)
            if value is not None:
                o[key] = value
        if self.subtitles_langs:
            langs = [lang for lang in self.subtitles_langs if lang and len(lang) <= 20 and lang.replace("-", "").replace("_", "").isalnum()]
            if langs:
                o["subtitles"] = {"langs": langs, "auto": bool(self.subtitles_auto),
                                  "format": self.subtitles_format or "srt", "embed": bool(self.subtitles_embed)}
        return o


class CreateDownloadRequest(BaseModel):
    url: str
    format_id: str
    is_playlist: bool = False
    filename: Optional[str] = None
    save_dir: Optional[str] = None
    category: Optional[str] = None
    engine: Optional[str] = Field(default=None, pattern=r"^(media|torrent|http)$")
    options: Optional[DownloadOptions] = None

    @field_validator("url")
    @classmethod
    def not_blank(cls, v):
        return _clean_url(v)

    @field_validator("format_id")
    @classmethod
    def format_id_reasonable(cls, v):
        v = (v or "").strip()
        if not v or len(v) > 220 or any(c in v for c in "\r\n\x00"):
            raise ValueError("invalid format_id")
        return v

    @field_validator("filename", "save_dir", "category")
    @classmethod
    def blank_to_none(cls, v):
        if v is not None and not v.strip():
            return None
        return v.strip() if v else v


class AutoDownloadRequest(BaseModel):
    """'Send this link to MDM' from the browser: engine and format are chosen
    automatically from the user's defaults."""
    url: str
    referer: Optional[str] = None
    title: Optional[str] = None

    @field_validator("url")
    @classmethod
    def not_blank(cls, v):
        return _clean_url(v)

    @field_validator("referer")
    @classmethod
    def referer_http_only(cls, v):
        if v and (not v.startswith(("http://", "https://")) or len(v) > _MAX_URL or any(c in v for c in "\r\n")):
            return None
        return v


class SettingsUpdate(BaseModel):
    download_dir: Optional[str] = None
    video_dir: Optional[str] = None
    audio_dir: Optional[str] = None
    ask_where_to_save: Optional[bool] = None
    max_concurrent_downloads: Optional[int] = Field(default=None, ge=1, le=16)
    max_retries: Optional[int] = Field(default=None, ge=0, le=10)
    download_timeout: Optional[int] = Field(default=None, ge=5, le=600)
    appearance_mode: Optional[str] = Field(default=None, pattern=r"^(Dark|Light)$")
    minimize_to_tray: Optional[bool] = None
    start_with_windows: Optional[bool] = None
    notifications_enabled: Optional[bool] = None
    clipboard_monitor_enabled: Optional[bool] = None
    ffmpeg_path: Optional[str] = None

    def changed_fields(self):
        return {k: v for k, v in self.model_dump().items() if v is not None}


class OpenRequest(BaseModel):
    """A link/path handed over by a second launch of the app (may be empty
    to just bring the window to the front)."""
    url: Optional[str] = None

    @field_validator("url")
    @classmethod
    def clean(cls, v):
        if v is None or not v.strip():
            return None
        return _clean_url(v)
