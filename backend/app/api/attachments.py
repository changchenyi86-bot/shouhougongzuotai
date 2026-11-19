"""附件上传、下载。"""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import attachments
from app.api.sending import require_agent
from app.db import get_session
from app.models import Attachment
from app.schemas import AttachmentOut

router = APIRouter(prefix="/api")


@router.post("/attachments", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload(
    file: UploadFile = File(...),
    operator: str | None = Form(None, max_length=64),
    session: Session = Depends(get_session),
) -> Attachment:
    """上传要发到群里的图片或文件。"""
    data = await file.read(attachments.max_bytes() + 1)
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "文件是空的")
    try:
        att = attachments.save(session, data, file.filename or "", "upload", operator)
    except attachments.AttachmentTooLarge as exc:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, str(exc)) from exc
    session.commit()
    return att


def _file(session: Session, att_id: uuid.UUID, download: bool) -> FileResponse:
    att = session.get(Attachment, att_id)
    path = attachments.path_of(att_id)
    if att is None or not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "文件不存在")
    disposition = "attachment" if download or not att.is_image else "inline"
    return FileResponse(path, media_type=att.mime, headers={
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(att.filename)}",
        "X-Content-Type-Options": "nosniff",
    })


@router.get("/attachments/{att_id}")
def download(att_id: uuid.UUID, download: bool = False, session: Session = Depends(get_session)) -> FileResponse:
    """图片在浏览器里直接显示，其他文件下载。"""
    return _file(session, att_id, download)


@router.get("/agent/attachments/{att_id}", dependencies=[Depends(require_agent)])
def agent_download(att_id: uuid.UUID, session: Session = Depends(get_session)) -> FileResponse:
    """发送程序下载要发出的附件。"""
    return _file(session, att_id, True)
