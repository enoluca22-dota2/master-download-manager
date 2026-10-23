"""File-type categories for the sidebar (Compressed, Programs, Videos, ...).

Same idea as AB Download Manager's default categories — classify by the
file's extension — extended with MDM's own engines: anything from the
torrent engine is a Torrent, anything from a media site is also listed
under "Media sites" (YouTube & co.). Classification is display-only; it never
decides how something is downloaded.
"""

import os
from urllib.parse import unquote, urlparse

CATEGORY_EXTS = {
    "Compressed": {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz", "zst", "lz", "lzma", "cab",
                   "arj", "z", "iso", "img", "dmg", "vhd", "vhdx", "wim"},
    "Programs": {"exe", "msi", "msix", "msixbundle", "appx", "appxbundle", "bat", "cmd", "apk", "xapk", "aab",
                 "deb", "rpm", "pkg", "appimage", "run", "bin", "jar", "sh", "flatpak", "snap"},
    "Videos": {"mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv", "mpg", "mpeg", "ts", "m2ts", "3gp",
               "ogv", "vob", "rm", "rmvb"},
    "Music": {"mp3", "m4a", "aac", "opus", "ogg", "oga", "flac", "wav", "wma", "alac", "aiff", "mka", "weba",
              "ape", "mid", "midi"},
    "Pictures": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg", "ico", "heic", "heif", "avif",
                 "psd", "raw", "cr2", "nef"},
    "Documents": {"pdf", "epub", "mobi", "azw3", "djvu", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods",
                  "odp", "rtf", "txt", "md", "csv", "json", "xml", "srt", "vtt", "ass"},
}
FILE_CATEGORIES = tuple(CATEGORY_EXTS)  # display order
TORRENTS = "Torrents"
MEDIA_SITES = "Media sites"
OTHER = "Other"

_EXT_TO_CATEGORY = {ext: cat for cat, exts in CATEGORY_EXTS.items() for ext in exts}
_KIND_TO_CATEGORY = {"video": "Videos", "audio": "Music", "image": "Pictures", "subtitle": "Documents"}


def extension_of(name):
    return os.path.splitext(name or "")[1].lstrip(".").lower()


def _url_ext(url):
    try:
        return extension_of(unquote(urlparse(url or "").path))
    except ValueError:
        return ""


def file_category(name="", engine=None, kind=None, url=""):
    """The sidebar category of one download."""
    if engine == "torrent" or kind == "torrent":
        return TORRENTS
    for candidate in (extension_of(name), _url_ext(url) if engine == "http" else ""):
        if candidate in _EXT_TO_CATEGORY:
            return _EXT_TO_CATEGORY[candidate]
    return _KIND_TO_CATEGORY.get(kind, OTHER)


def job_category(job):
    name = job.result_path or job.filename_override or job.title or ""
    return file_category(name, job.engine, job.kind, job.url)


def is_media_site(job):
    return job.engine in ("media", "youtube")
