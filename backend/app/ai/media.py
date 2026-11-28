"""客户附件识别：图片调用识图接口，文档在本地提取文字。"""

import base64
import io
import logging
import re
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from docx import Document
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader

from app import attachments, db
from app.ai import usage
from app.config import settings
from app.models import Attachment, Message

log = logging.getLogger(__name__)
_locks = {}
_locks_guard = threading.Lock()
_TEXT_EXT = {'.txt', '.csv', '.log', '.json', '.md', '.sh', '.sql', '.yaml', '.yml', '.xml', '.ini', '.conf'}
_PROMPT = ("这是客户在售后技术支持群里发的截图。请先逐字抄出弹框、报错、提示信息的原文（最重要，放最前面），"
           "再抄其余文字：页面上的网址、菜单和按钮名称、表格内容，按区域分行；浏览器书签栏、系统任务栏这类和问题无关的不用抄。"
           "看不清的字标「?」，不要猜。然后另起一段，以「说明：」开头，用一两句话说清"
           "这是什么系统的什么界面、处于什么状态（如报错、空白、加载中、操作结果）。"
           "只描述图里能看到的，不要推测原因，不要给解决办法。")


class SkipMedia(Exception):
    pass


def _lock(attachment_id):
    with _locks_guard:
        return _locks.setdefault(attachment_id, threading.Lock())


def is_running(attachment_id) -> bool:
    return _lock(attachment_id).locked()


def _truncate(value: str, limit: int) -> str:
    suffix = '…（已截断）'
    if len(value) <= limit:
        return value
    return value[:max(0, limit - len(suffix))] + suffix[:max(0, limit)]


def prompt_text(attachment: Attachment, limit: int | None = None) -> str | None:
    if not attachment or attachment.extract_status != 'done' or not attachment.extracted_text:
        return None
    value = re.sub(r'\s*\n\s*', ' / ', attachment.extracted_text.strip())
    return _truncate(re.sub(r'\s+', ' ', value), settings.media_prompt_max_chars if limit is None else limit)


def image_data_url(data: bytes, max_side: int = 2000) -> str:
    """把图片缩到最长边不超过 max_side，转成发给模型的 data URL（有透明背景用 PNG，否则 JPEG）。"""
    with Image.open(io.BytesIO(data)) as image:
        image.seek(0)
        image.thumbnail((max_side, max_side))
        transparent = image.mode in ('RGBA', 'LA') or 'transparency' in image.info
        image = image.convert('RGBA' if transparent else 'RGB')
        output = io.BytesIO()
        image.save(output, format='PNG' if transparent else 'JPEG')
    mime = 'image/png' if transparent else 'image/jpeg'
    return f'data:{mime};base64,' + base64.b64encode(output.getvalue()).decode('ascii')


_VIDEO_PROMPT = ("这是客户在售后技术支持群里发的一段录屏或视频，按时间顺序均匀抽了几帧，从左到右、从上到下拼成一张图，"
                 "每格左上角标了帧序号和时间。请按帧顺序说明画面里客户在做什么操作、界面怎么变化，"
                 "逐字抄出出现的弹框、报错、提示信息原文，以及关键的菜单、按钮名称和网址；看不清的字标「?」，不要猜。"
                 "最后另起一段，以「说明：」开头，用一两句话概括这段视频演示了什么问题。只描述画面里能看到的，不要推测原因。")


def frames_path(attachment_id) -> Path:
    """视频抽帧拼图存在附件文件旁边，写建议时直接给模型看。"""
    return Path(str(attachments.path_of(attachment_id)) + '.frames.jpg')


def video_frames(data: bytes, count: int) -> tuple[bytes, float, int]:
    """从视频里按时间均匀抽 count 帧，拼成一张 JPEG。返回 (图片, 时长秒, 实际帧数)。"""
    import av
    from PIL import ImageDraw, ImageFont

    with av.open(io.BytesIO(data)) as container:
        stream = container.streams.video[0]
        duration = float(stream.duration * stream.time_base) if stream.duration else (
            container.duration / 1_000_000 if container.duration else 0.0)
        frames = []
        if duration > 0:
            # 避开第一帧（录屏开头常是黑屏）和最后一帧，均匀取点后各解出最近的一帧
            for i in range(count):
                at = duration * (i + 0.5) / count
                container.seek(int(at / stream.time_base), stream=stream, any_frame=False, backward=True)
                for frame in container.decode(stream):
                    if frame.time is None or frame.time >= at - 0.05:
                        frames.append((frame.time if frame.time is not None else at, frame.to_image()))
                        break
        if not frames:  # 时长未知：顺序解码，按间隔取
            all_frames = [(f.time or 0.0, f.to_image()) for f in container.decode(stream)]
            step = max(1, len(all_frames) // count)
            frames = all_frames[step // 2::step][:count]
    if not frames:
        raise ValueError('视频里没有解出画面')
    cols = 3 if len(frames) > 4 else 2 if len(frames) > 1 else 1
    rows = (len(frames) + cols - 1) // cols
    cell = 640
    tiles = []
    for _, image in frames:
        image = image.convert('RGB')
        image.thumbnail((cell, cell))
        tiles.append(image)
    width, height = max(t.width for t in tiles), max(t.height for t in tiles)  # 竖屏视频格子也是竖的，不留大片白边
    sheet = Image.new('RGB', (cols * width, rows * height), 'white')
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=22)
    for i, ((at, _), tile) in enumerate(zip(frames, tiles)):
        x, y = (i % cols) * width, (i // cols) * height
        sheet.paste(tile, (x + (width - tile.width) // 2, y))
        label = f'#{i + 1} {int(at) // 60}:{int(at) % 60:02d}'
        right = int(draw.textlength(label, font=font)) + 12
        draw.rectangle((x, y, x + right, y + 30), fill='black')
        draw.text((x + 6, y + 3), label, fill='white', font=font)
    output = io.BytesIO()
    sheet.save(output, format='JPEG', quality=85)
    return output.getvalue(), duration, len(frames)


def _image(data: bytes, timeout: float | None, prompt: str = _PROMPT, max_side: int = 2000) -> tuple[str, str]:
    base = settings.vision_base_url or settings.llm_base_url
    key = settings.vision_api_key if settings.vision_base_url else (settings.vision_api_key or settings.llm_api_key)
    model = settings.vision_model or settings.llm_model
    if not base or not key or not model:
        raise SkipMedia('未配置识图接口')
    deadline = time.monotonic() + timeout if timeout is not None else None
    url = image_data_url(data, max_side)
    errors = []
    for name in [model] + ([settings.vision_fallback_model] if settings.vision_fallback_model else []):
        remaining = deadline - time.monotonic() if deadline is not None else settings.vision_timeout
        if remaining <= 0:
            errors.append('识图等待超时')
            break
        started, body = time.monotonic(), None
        try:
            response = httpx.post(base.rstrip('/') + '/chat/completions',
                                  headers={'Authorization': f'Bearer {key}'},
                                  json={'model': name, 'max_tokens': settings.vision_max_tokens,
                                        'messages': [{'role': 'user', 'content': [
                                            {'type': 'text', 'text': prompt},
                                            {'type': 'image_url', 'image_url': {'url': url}}]}]},
                                  timeout=min(settings.vision_timeout, remaining))
            response.raise_for_status()
            body = response.json()
            _record_vision(body, name, started)
            choice = body['choices'][0]
            content = choice['message'].get('content')
            if not isinstance(content, str) or not content.strip():
                if choice.get('finish_reason') == 'length':
                    raise ValueError(f'模型思考用完了 {settings.vision_max_tokens} 个 token 还没写出结果，可调大 VISION_MAX_TOKENS')
                raise ValueError('识图接口返回空内容')
            return content.strip(), body.get('model') or name
        except Exception as exc:
            if body is None:  # 接口没回（超时、报错）；回了的上面已经记过
                _record_vision(None, name, started, error=f'{type(exc).__name__}: {exc}')
            errors.append(f'{name}: {type(exc).__name__}: {str(exc)[:300]}')
    raise ValueError('；'.join(errors))


def _record_vision(body, model: str, started: float, error: str | None = None) -> None:
    """识图的 token：图片本身折成的 token 估不准，接口没返回用量时只记次数。"""
    tokens = usage.openai_tokens(body.get('usage') if isinstance(body, dict) else None)
    i, o, c = tokens or (0, 0, 0)
    actual = body.get('model') if isinstance(body, dict) else None
    usage.record('vision', actual or model, i, o, c,
                 ok=error is None, estimated=tokens is None and error is None,
                 duration_ms=int((time.monotonic() - started) * 1000), error=error)


def _kind(data: bytes, filename: str, mime: str) -> str:
    ext = Path(filename).suffix.lower()
    sniffed, _ = attachments.sniff(data[:16], '')
    if sniffed.startswith('image/'):
        return 'image'
    if data[4:8] == b'ftyp' or mime.startswith('video/'):
        return 'video'
    if data.startswith(b'%PDF-'):
        return 'pdf'
    if data.startswith(b'\xd0\xcf\x11\xe0'):
        return 'xls' if ext == '.xls' else 'doc' if ext == '.doc' else 'other'
    if data.startswith(b'PK\x03\x04'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if any(n.startswith('xl/') for n in names):
                return 'excel'
            if any(n.startswith('word/') for n in names):
                return 'word'
        return 'other'
    if data.startswith((b'\x1f\x8b', b'Rar!')) or data[257:262] == b'ustar':
        return 'other'
    if ext in ('.xls', '.doc'):
        return ext[1:]
    if ext in ('.xlsx', '.xlsm'):
        return 'excel'
    if ext == '.docx':
        return 'word'
    if ext == '.pdf':
        return 'pdf'
    if ext in _TEXT_EXT or mime.startswith('text/'):
        return 'text'
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
        return 'image'
    return 'other'


def _parse(data: bytes, attachment: Attachment, timeout: float | None) -> tuple[str, str]:
    kind = _kind(data, attachment.filename, attachment.mime)
    if kind == 'image':
        return _image(data, timeout)
    if kind == 'video':
        sheet, duration, count = video_frames(data, max(1, settings.video_frames))
        frames_path(attachment.id).write_bytes(sheet)
        text, model = _image(sheet, timeout, _VIDEO_PROMPT, max_side=1920)
        return f'（视频约 {int(round(duration))} 秒，按时间抽了 {count} 帧）\n{text}', model
    if kind in ('xls', 'doc'):
        raise SkipMedia(f'暂不支持 {kind} 老格式')
    if kind == 'excel':
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            parts = []
            for sheet in workbook:
                size = f'{sheet.max_row}行×{sheet.max_column}列' if sheet.max_row and sheet.max_column else '行列数未知'
                parts.append(f'{sheet.title}：{size}')
                for row in sheet.iter_rows(max_row=11, values_only=True):
                    if any(v is not None and str(v).strip() for v in row):
                        parts.append(' | '.join('' if v is None else str(v) for v in row))
            return '\n'.join(parts), 'openpyxl'
        finally:
            workbook.close()
    if kind == 'word':
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        parts += [' | '.join(cell.text for cell in row.cells) for table in doc.tables for row in table.rows]
        return '\n'.join(parts), 'python-docx'
    if kind == 'pdf':
        reader = PdfReader(io.BytesIO(data))
        value = '\n'.join(page.extract_text() or '' for page in reader.pages[:5])
        if len(re.sub(r'\s+', '', value)) < 20:
            raise SkipMedia('PDF 没有文字层（可能是扫描件），暂不识别')
        return value, 'pypdf'
    if kind == 'text':
        for encoding in ('utf-8', 'gb18030'):
            try:
                return data.decode(encoding), 'text'
            except UnicodeDecodeError:
                continue
        raise ValueError('文本无法按 UTF-8 或 GB18030 解码')
    raise SkipMedia('暂不支持这种文件')


def extract(session, attachment: Attachment, timeout: float | None = None, force: bool = False) -> None:
    """识别并提交，已完成的附件不会重复处理；任何异常都不向调用方传播。"""
    try:
        deadline = time.monotonic() + timeout if timeout is not None else None
        retry = False
        attachment_id = attachment.id
        lock = _lock(attachment_id)
        acquired = lock.acquire() if timeout is None else lock.acquire(timeout=max(0, timeout))
        if not acquired:
            return
        try:
            session.refresh(attachment)
            if attachment.extract_status in ('done', 'failed', 'skipped') and not force:
                return
            try:
                if attachment.source != 'channel':
                    raise SkipMedia('非客户渠道附件，无需识别')
                data = attachments.path_of(attachment_id).read_bytes()
                remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
                value, model = _parse(data, attachment, remaining)
                attachment.extracted_text = _truncate(value, max(0, settings.media_text_max_chars))
                attachment.extract_status, attachment.extract_error, attachment.extract_model = 'done', None, model[:128]
            except SkipMedia as exc:
                attachment.extract_status, attachment.extract_error = 'skipped', str(exc)[:500]
                attachment.extracted_text, attachment.extract_model = None, None
            except Exception as exc:
                if deadline is not None and time.monotonic() >= deadline:
                    # 只是调用方（生成建议）等不及了，不算识别失败：保持待识别，交给后台不限时再识别一次
                    log.info('附件识别未在等待时间内完成，转后台继续 attachment=%s: %s', attachment_id, exc)
                    retry = True
                else:
                    log.warning('附件识别失败 attachment=%s: %s', attachment_id, exc)
                    attachment.extract_status, attachment.extract_error = 'failed', f'{type(exc).__name__}: {str(exc)[:500]}'
                    attachment.extracted_text, attachment.extract_model = None, None
            if not retry:
                attachment.extracted_at = datetime.now(timezone.utc)
            session.commit()
        finally:
            lock.release()
        if retry:
            from app.ai import worker

            worker.submit(run, attachment_id)
    except Exception:
        log.exception('保存附件识别结果失败')
        try:
            session.rollback()
        except Exception:
            log.exception('回滚附件识别结果失败')


def run(attachment_id) -> None:
    try:
        with db.SessionLocal() as session:
            attachment = session.get(Attachment, attachment_id)
            if attachment is None:
                return
            message = session.query(Message).filter(Message.attachment_id == attachment_id).first()
            with usage.scope(**usage.for_message(message)):
                extract(session, attachment)
            session.refresh(attachment)
            done = attachment.extract_status == 'done'
    except Exception:
        log.exception('后台附件识别失败 attachment=%s', attachment_id)
        return
    if done:
        try:
            from app.ai import worker

            worker.after_media(attachment_id)
        except Exception:
            log.exception('附件识别后重新生成建议失败 attachment=%s', attachment_id)
