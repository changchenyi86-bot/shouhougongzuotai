MSG = {
    "channel": "feishu",
    "external_id": "om_123",
    "group_id": "oc_abc",
    "group_name": "某客户-有数项目群",
    "is_external_group": True,
    "sender_id": "ou_1",
    "sender_name": "张三",
    "content": "报表导出一直转圈怎么办？",
    "sent_at": "2026-11-19T08:00:00Z",
    "raw": {"any": "payload"},
}


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_ingest_and_list(client):
    r = client.post("/api/messages", json=MSG)
    assert r.status_code == 201
    body = r.json()
    assert body["channel"] == "feishu" and "raw" not in body

    # 重复推送去重
    assert client.post("/api/messages", json=MSG).json()["id"] == body["id"]

    rows = client.get("/api/messages", params={"channel": "feishu"}).json()
    assert len(rows) == 1 and rows[0]["content"] == MSG["content"]
    assert rows[0]["is_external_group"] is True
    assert client.get("/api/messages", params={"channel": "wecom"}).json() == []


def test_invalid_channel(client):
    assert client.post("/api/messages", json={**MSG, "channel": "qq"}).status_code == 422


def test_webhook_not_implemented(client):
    assert client.post("/webhooks/dingtalk", json={}).status_code == 501
