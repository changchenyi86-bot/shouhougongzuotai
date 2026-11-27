"""附件存储：文件按 id 存在 settings.attachment_dir 下，数据库只记元信息。"""

import hashlib
import mimetypes
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Attachment

# 常见图片的文件头，渠道下载的图片没有扩展名，靠它判断类型
_MAGIC = [(b"\xff\xd8\xff", "image/jpeg", ".jpg"), (b"\x89PNG", "image/png", ".png"),
          (b"GIF8", "image/gif", ".gif"), (b"BM", "image/bmp", ".bmp")]


class AttachmentTooLarge(Exception):
    pass


def max_bytes(video: bool = False) -> int:
    return (settings.video_max_mb if video else settings.attachment_max_mb) * 1024 * 1024


def path_of(att_id: uuid.UUID) -> Path:
    return Path(settings.attachment_dir) / att_id.hex


def sniff(data: bytes, filename: str) -> tuple[str, str]:
    """返回 (mime, 文件名)。没有扩展名的图片按文件头补上扩展名。"""
    for magic, mime, ext in _MAGIC:
        if data.startswith(magic):
            if not Path(filename).suffix:
                filename += ext
            return mime, filename
    if data[4:8] == b"ftyp":  # MP4 / MOV 视频
        mime, ext = ("video/quicktime", ".mov") if data[8:12] == b"qt  " else ("video/mp4", ".mp4")
        return mime, filename if Path(filename).suffix else filename + ext
    if data[8:12] == b"WEBP":
        return "image/webp", filename if Path(filename).suffix else filename + ".webp"
    return mimetypes.guess_type(filename)[0] or "application/octet-stream", filename


def clean_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip() or "未命名文件"
    return name[:200]


def save(session: Session, data: bytes, filename: str, source: str, operator: str | None = None,
         limit: int | None = None) -> Attachment:
    """保存附件（调用方负责 commit）。limit 为字节上限，默认 attachment_max_mb。"""
    limit = limit or max_bytes()
    if len(data) > limit:
        raise AttachmentTooLarge(f"文件超过 {limit // (1024 * 1024)}MB")
    mime, filename = sniff(data, clean_name(filename))
    att = Attachment(id=uuid.uuid4(), filename=filename, mime=mime, size=len(data),
                     sha256=hashlib.sha256(data).hexdigest(), source=source, created_by=operator)
    path = path_of(att.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    session.add(att)
    session.flush()
    return att
