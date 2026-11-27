"""附件识别离线测试。"""
import base64
import io
import json
import zipfile
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from docx import Document
from openpyxl import Workbook
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app import attachments, db
from app.ai import media, suggest
from app.channels.wecom import media as wecom_media
from app.channels.wecom.archive import ArchiveSyncer, set_syncer
from app.config import settings
from app.models import Message
from tests.test_round_context import collect, post, run_pending, snapshot
from tests.test_workspace import MediaArchive
from tests.test_wecom import CUST, KEY, FakeDirectory, _item, rec


@pytest.fixture(autouse=True)
def config(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'attachment_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'llm_base_url', 'https://example.invalid/v1')
    monkeypatch.setattr(settings, 'llm_api_key', 'test-key')
    monkeypatch.setattr(settings, 'llm_model', 'primary')
    monkeypatch.setattr(settings, 'vision_base_url', '')
    monkeypatch.setattr(settings, 'vision_api_key', '')
    monkeypatch.setattr(settings, 'vision_model', '')
    monkeypatch.setattr(settings, 'vision_fallback_model', '')
    monkeypatch.setattr(media.httpx, 'post', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('禁止网络')))


def image_bytes(size=(20, 10)):
    out = io.BytesIO()
    Image.new('RGB', size, 'white').save(out, 'PNG')
    return out.getvalue()


def save(session, data, name='image.png', source='channel'):
    att = attachments.save(session, data, name, source)
    session.commit()
    return att


def mock_vision(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    def send(url, **kwargs):
        with httpx.Client(transport=transport) as client:
            return client.post(url, **kwargs)
    monkeypatch.setattr(media.httpx, 'post', send)


def success(request, model='server-model'):
    return httpx.Response(200, json={'model': model, 'choices': [{'message': {'content': '网址 / 报错\n说明：界面'}}]})


def test_image_resize_request_and_idempotent(monkeypatch):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert 'response_format' not in body
        part = body['messages'][0]['content'][1]
        assert part['type'] == 'image_url' and part['image_url']['url'].startswith('data:image/jpeg;base64,')
        with Image.open(io.BytesIO(base64.b64decode(part['image_url']['url'].split(',')[1]))) as img:
            assert img.size == (2000, 667)
        return success(request)
    mock_vision(monkeypatch, handler)
    with db.SessionLocal() as session:
        att = save(session, image_bytes((3000, 1000)))
        media.extract(session, att)
        assert (att.extract_status, att.extract_model) == ('done', 'server-model')
        assert att.extracted_at and media.prompt_text(att) == '网址 / 报错 / 说明：界面'
        media.extract(session, att)
        assert len(calls) == 1


def test_fallback_and_separate_address_key(monkeypatch):
    monkeypatch.setattr(settings, 'vision_fallback_model', 'fallback')
    seen = []
    def handler(request):
        name = json.loads(request.content)['model']
        seen.append(name)
        return httpx.Response(500) if name == 'primary' else success(request, 'fallback')
    mock_vision(monkeypatch, handler)
    with db.SessionLocal() as session:
        att = save(session, image_bytes())
        media.extract(session, att)
        assert seen == ['primary', 'fallback'] and att.extract_model == 'fallback' and att.extract_status == 'done'
        monkeypatch.setattr(settings, 'vision_base_url', 'https://other.invalid/v1')
        att = save(session, image_bytes())
        media.extract(session, att)
        assert att.extract_status == 'skipped' and att.extract_error == '未配置识图接口' and len(seen) == 2


def pdf_bytes(with_text):
    writer = PdfWriter()
    page = writer.add_blank_page(width=400, height=200)
    if with_text:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                                 NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 10 150 Td (This PDF contains sufficient text for extraction.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_document_formats_and_corruption():
    book = Workbook()
    book.active.title = '第一表'
    book.active.append(['表头', '数值'])
    book.active.append(['结果', 42])
    book.create_sheet('第二表').append(['另一表'])
    excel = io.BytesIO()
    book.save(excel)
    doc = Document()
    doc.add_paragraph('一段正文')
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = '甲', '乙'
    word = io.BytesIO()
    doc.save(word)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('a.txt', 'hello')
    with db.SessionLocal() as session:
        for data, name, expected, model in [(excel.getvalue(), 'a.xlsx', '第二表', 'openpyxl'),
                (word.getvalue(), 'a.docx', '甲 | 乙', 'python-docx'), ('中文日志'.encode('gb18030'), 'a.txt', '中文日志', 'text'),
                (pdf_bytes(True), 'a.pdf', 'sufficient text', 'pypdf')]:
            att = save(session, data, name)
            att.mime = 'application/octet-stream'
            session.commit()
            media.extract(session, att)
            assert att.extract_status == 'done' and expected in att.extracted_text and att.extract_model == model
        for data, name, reason in [(b'old', 'old.xls', 'xls'), (b'old', 'old.doc', 'doc'),
                                   (archive.getvalue(), 'a.zip', '暂不支持'), (pdf_bytes(False), 'blank.pdf', '没有文字层')]:
            att = save(session, data, name)
            media.extract(session, att)
            assert att.extract_status == 'skipped' and reason in att.extract_error
        att = save(session, b'hi', 'upload.txt', 'upload')
        media.extract(session, att)
        assert att.extract_status == 'skipped'
        att = save(session, b'%PDF-broken', 'bad.pdf')
        media.extract(session, att)
        assert att.extract_status == 'failed' and att.extract_error


def test_truncation_migration_and_retry(monkeypatch):
    assert {'extracted_text', 'extract_status', 'extract_error', 'extract_model', 'extracted_at'} <= set(db._NEW_COLUMNS['attachments'])
    monkeypatch.setattr(settings, 'media_text_max_chars', 12)
    with db.SessionLocal() as session:
        att = save(session, '一二三四五六七八九十甲乙丙丁'.encode(), 'a.txt')
        att.extract_status = 'failed'
        session.commit()
        media.extract(session, att)
        assert att.extract_status == 'failed'
        media.extract(session, att, force=True)
        assert len(att.extracted_text) == 12 and att.extracted_text.endswith('…（已截断）')
        assert len(media.prompt_text(att, 8)) == 8
        with media._lock(att.id):
            assert media.is_running(att.id)
            media.extract(session, att, timeout=0.01)
        assert not media.is_running(att.id)


def test_fetch_and_message_api(client, monkeypatch):
    monkeypatch.setattr(wecom_media, 'submit', wecom_media.fetch)
    mock_vision(monkeypatch, success)
    blob = image_bytes()
    archive = MediaArchive([_item(1, rec('image1', CUST, msgtype='image', image={'sdkfileid': 'F1', 'filesize': len(blob)}))], {'F1': blob})
    syncer = ArchiveSyncer(lambda: archive, {'1': KEY}, FakeDirectory())
    set_syncer(syncer)
    try:
        assert syncer.sync_once() == 1
        page = client.get('/api/conversation', params={'channel': 'wecom', 'group_id': 'wrROOM1'}).json()
        att = page['items'][0]['message']['attachment']
        assert att['extract_status'] == 'done' and '网址' in att['extracted_text']
    finally:
        set_syncer(None)


@pytest.mark.parametrize('mode', ['success', 'failed', 'zero'])
def test_generate_waits_and_failure_does_not_block(client, fake_llm, monkeypatch, mode):
    pending = collect(monkeypatch)
    first = post(client, 1, '[图片]')
    with db.SessionLocal() as session:
        att = save(session, image_bytes())
        att.extract_status = 'pending'
        session.get(Message, first).attachment_id = att.id
        session.commit()
    last = post(client, 2, '一点击批量补数据就是这个样子', 1)
    if mode == 'zero':
        monkeypatch.setattr(settings, 'media_wait_seconds', 0)
    elif mode == 'failed':
        monkeypatch.setattr(settings, 'vision_fallback_model', 'fallback')
        mock_vision(monkeypatch, lambda request: httpx.Response(500))
    else:
        mock_vision(monkeypatch, success)
    run_pending(pending)
    assert snapshot(last).status.value == 'ready'
    draft = fake_llm.calls[-1][1]
    query = next(prompt for schema, prompt in fake_llm.generated if schema is suggest.RoundQuery)
    assert '[图片]' in draft and '[图片]' in query
    assert ('识别内容：' in draft) == (mode == 'success')
    assert ('识别内容：' in query) == (mode == 'success')
    assert 'data:image/' not in draft and 'data:image/' not in query
    with db.SessionLocal() as session:
        assert session.get(Message, first).attachment.extract_status == {'success': 'done', 'failed': 'failed', 'zero': 'pending'}[mode]


def test_timeout_and_empty_response(monkeypatch):
    requests = []
    monkeypatch.setattr(settings, 'vision_fallback_model', 'fallback')
    def handler(request):
        requests.append(json.loads(request.content)['model'])
        if len(requests) == 1:
            raise httpx.ReadTimeout('识图超时', request=request)
        return httpx.Response(200, json={'choices': [{'message': {'content': ''}}]})
    mock_vision(monkeypatch, handler)
    with db.SessionLocal() as session:
        att = save(session, image_bytes())
        media.extract(session, att, timeout=1)
        assert requests == ['primary', 'fallback']
        assert att.extract_status == 'failed' and '超时' in att.extract_error


def test_wait_for_background_result(monkeypatch):
    with db.SessionLocal() as session:
        att = save(session, '测试文字'.encode(), 'a.txt')
        msg = Message(channel='wecom', external_id='background', group_id='wrA', sender_id=CUST,
                      content='[文件]', sent_at=datetime.now(timezone.utc),
                      attachment_id=att.id)
        session.add(msg)
        session.commit()
        reads = []
        def running(_):
            reads.append(1)
            return len(reads) == 1
        def finish(_):
            with db.SessionLocal() as other:
                a = other.get(type(att), att.id)
                a.extract_status, a.extracted_text = 'done', '后台识别文字'
                other.commit()
        monkeypatch.setattr(media, 'is_running', running)
        monkeypatch.setattr(suggest.time, 'sleep', finish)
        suggest._wait_media(session, msg, [msg], [])
        assert att.extract_status == 'done' and '后台识别文字' in suggest._line(msg)


def test_wait_skips_old_attachments(monkeypatch):
    """一轮对话时间窗以前的附件不在生成建议时临时识别，留给补历史命令。"""
    calls = []
    monkeypatch.setattr(media, 'extract', lambda *args, **kwargs: calls.append(args))
    now = datetime.now(timezone.utc)
    with db.SessionLocal() as session:
        att = save(session, '旧文件'.encode(), 'old.txt')
        old = Message(channel='wecom', external_id='old-file', group_id='wrA', sender_id=CUST,
                      content='[文件]', sent_at=now - timedelta(hours=3), attachment_id=att.id)
        current = Message(channel='wecom', external_id='now-text', group_id='wrA', sender_id=CUST,
                          content='还是不行', sent_at=now)
        session.add_all([old, current])
        session.commit()
        suggest._wait_media(session, current, [current], [old])
        assert calls == [] and att.extract_status is None


@pytest.mark.parametrize('mode', ['timeout', 'unconfigured', 'corrupt', 'submit_error'])
def test_download_and_suggestion_survive_extract_errors(client, monkeypatch, mode):
    monkeypatch.setattr(wecom_media, 'submit', wecom_media.fetch)
    blob = b'\x89PNG\r\n\x1a\ncorrupt' if mode == 'corrupt' else image_bytes()
    if mode == 'timeout':
        def timeout(request):
            raise httpx.ReadTimeout('超时', request=request)
        mock_vision(monkeypatch, timeout)
    elif mode == 'unconfigured':
        monkeypatch.setattr(settings, 'llm_base_url', '')
    elif mode == 'submit_error':
        from app.ai import worker
        monkeypatch.setattr(worker, 'submit', lambda *a: (_ for _ in ()).throw(RuntimeError('提交失败')))
    archive = MediaArchive([_item(1, rec('img_error', CUST, msgtype='image', image={'sdkfileid': 'F1', 'filesize': len(blob)}))], {'F1': blob})
    syncer = ArchiveSyncer(lambda: archive, {'1': KEY}, FakeDirectory())
    set_syncer(syncer)
    try:
        assert syncer.sync_once() == 1
        page = client.get('/api/conversation', params={'channel': 'wecom', 'group_id': 'wrROOM1'}).json()
        item = page['items'][0]
        assert item['message']['attachment'] and item['message']['media_error'] is None
        assert item['message']['attachment']['extract_status'] == {'timeout': 'failed', 'unconfigured': 'skipped', 'corrupt': 'failed', 'submit_error': 'pending'}[mode]
        # 图片单独发出默认不自动生成建议，显式生成以验证错误不会阻断。
        if mode != 'submit_error':
            from app.ai import worker
            mid = uuid.UUID(item['message']['id'])
            worker.enqueue(mid, wait=0)
            assert snapshot(mid).status.value == 'ready'
    finally:
        set_syncer(None)
