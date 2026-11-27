"""同一轮里后到的图片、文件并进这一轮；识别晚出结果时重新生成。"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app import attachments, db
from app.ai import suggest, worker
from app.ai.llm import SuggestionDraft
from app.config import settings
from app.models import Attachment, Message, Suggestion, SuggestionStatus as S

CUSTOMER = "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345"
OTHER = "wmOtherCustomer0123456789abcdef"


def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


BASE = now() - timedelta(minutes=5)


def post(client, n, text, seconds=0, sender=CUSTOMER, msg_type="text"):
    response = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"m{n}", "group_id": "wrA", "group_name": "客户项目群",
        "is_external_group": True, "sender_id": sender, "sender_name": "张三" if sender == CUSTOMER else "夜兮",
        "content": text, "msg_type": msg_type, "sent_at": (BASE + timedelta(seconds=seconds)).isoformat(),
    })
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def snapshot(mid):
    with db.SessionLocal() as session:
        return session.scalar(select(Suggestion).where(Suggestion.message_id == mid))


def attach(mid, status="pending", text=None):
    with db.SessionLocal() as session:
        att = attachments.save(session, b"\x89PNG fake", "a.png", "channel")
        att.extract_status, att.extracted_text = status, text
        if status == "done":
            att.extracted_at = datetime.now(timezone.utc)
        session.get(Message, mid).attachment_id = att.id
        session.commit()
        return att.id


def finish(att_id, text):
    with db.SessionLocal() as session:
        att = session.get(Attachment, att_id)
        att.extract_status, att.extracted_text = "done", text
        att.extracted_at = datetime.now(timezone.utc)
        session.commit()


def test_images_after_question_join_round(client, fake_llm):
    question = post(client, 1, "看下21有啥问题")
    assert snapshot(question).status == S.READY
    images = [post(client, 2, "[图片]", 120, sender=OTHER, msg_type="image"),
              post(client, 3, "[图片]", 121, sender=OTHER, msg_type="image")]
    last = snapshot(images[-1])
    assert last.status == S.READY and last.covers == [str(question), *map(str, images)]
    for mid in [question, images[0]]:
        assert snapshot(mid).status == S.MERGED and snapshot(mid).merged_into == last.id
    assert "<current_round>" in fake_llm.calls[-1][1] and "看下21有啥问题" in fake_llm.calls[-1][1].split("<current_round>")[1]


def test_lone_or_answered_images_do_not_start_round(client):
    lone = post(client, 1, "[图片]", msg_type="image")
    assert snapshot(lone) is None
    question = post(client, 2, "导出报错？", 10)
    post(client, 3, "请刷新再试", 20, sender="lisi")
    after_reply = post(client, 4, "[文件] 日志.txt", 30, msg_type="file")
    assert snapshot(after_reply) is None and snapshot(question).status == S.READY
    file_in_round = post(client, 5, "[文件] 报错.log", 40, msg_type="file")
    assert snapshot(file_in_round) is None  # 我方回复后客户只发文件，没有没处理完的一轮


def test_late_recognition_regenerates_until_handled(client, fake_llm):
    question = post(client, 1, "看下21有啥问题")
    image = post(client, 2, "[图片]", 30, sender=OTHER, msg_type="image")
    att = attach(image)
    worker.enqueue(image, wait=0)  # 生成时识别还没出来
    calls = len(fake_llm.calls)
    assert "SQL 报错 column online" not in fake_llm.calls[-1][1]
    finish(att, "SQL 报错 column online")
    worker.after_media(att)
    assert len(fake_llm.calls) == calls + 1 and "SQL 报错 column online" in fake_llm.calls[-1][1]
    assert snapshot(image).status == S.READY and snapshot(question).status == S.MERGED

    # 同事已经接手（转人工）：识别再出结果也不动
    assert client.post(f"/api/suggestions/{snapshot(image).id}/escalate", json={}).status_code == 200
    finish(att, "又识别了一次")
    worker.after_media(att)
    assert len(fake_llm.calls) == calls + 1


def test_recognition_already_used_or_merged_chain(client, fake_llm):
    post(client, 1, "看下这个")
    image = post(client, 2, "[图片]", 30, msg_type="image")
    att = attach(image, "done", "已经识别好的内容")
    worker.enqueue(image, wait=0)
    calls = len(fake_llm.calls)
    worker.after_media(att)  # 生成时已经用上了，不重新生成
    assert len(fake_llm.calls) == calls
    later = post(client, 3, "还有这个报错？", 40)
    assert snapshot(image).merged_into == snapshot(later).id
    finish(att, "后来重新识别的内容")
    worker.after_media(att)  # 沿着并入关系找到当前那张建议卡
    assert len(fake_llm.calls) == calls + 2 and snapshot(later).status == S.READY


def test_auto_dismissed_round_reopens_after_recognition(client, fake_llm):
    fake_llm.draft_result = SuggestionDraft(needs_reply=False, can_answer=True, confidence="high", reply="收到",
                                            reason="", references=[])
    post(client, 1, "看下")
    image = post(client, 2, "[图片]", 5, msg_type="image")
    att = attach(image)
    worker.enqueue(image, wait=0)
    assert snapshot(image).status == S.DISMISSED and snapshot(image).auto_dismissed
    fake_llm.draft_result = SuggestionDraft(needs_reply=True, can_answer=True, confidence="high", reply="这是字段缺失",
                                            reason="", references=[])
    finish(att, "ERROR 1054 Unknown column")
    worker.after_media(att)
    sug = snapshot(image)
    assert sug.status == S.READY and not sug.auto_dismissed and sug.content == "这是字段缺失"


def test_recognition_finishing_during_generation_runs_again(client, fake_llm, monkeypatch):
    post(client, 1, "看下21")
    image = post(client, 2, "[图片]", 5, msg_type="image")
    att = attach(image)
    original = suggest.generate
    state = {"n": 0}

    def generate(*args, **kwargs):
        result = original(*args, **kwargs)
        state["n"] += 1
        if state["n"] == 1:  # 提示词拼好之后识别才完成
            finish(att, "识别晚到的内容")
        return result

    monkeypatch.setattr(suggest, "generate", generate)
    calls = len(fake_llm.calls)
    worker.enqueue(image, wait=0)
    assert state["n"] == 2 and "识别晚到的内容" in fake_llm.calls[-1][1]
    assert len(fake_llm.calls) > calls and snapshot(image).status == S.READY


def test_window_zero_keeps_old_behaviour(client, monkeypatch):
    monkeypatch.setattr(settings, "ai_round_window_minutes", 0)
    post(client, 1, "看下")
    assert snapshot(post(client, 2, "[图片]", 5, msg_type="image")) is None
