# 售后技术支持工作台

用 AI 重塑有数 & 知数的售后技术支持流程：把企业微信、钉钉、飞书群里的客户问题集中到一个工作台，AI 预处理并给出回复建议；AI 解答不了的转给技术支持，处理结果沉淀进知识库，同类问题下次自动解决；同时支持内部建工单和技术支持积分排名。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2 |
| 数据库 | PostgreSQL 16 + pgvector（知识库向量检索） |
| 前端 | React 18 + TypeScript（Vite） |
| 部署 | docker-compose |

## 目录

```
backend/
  app/
    main.py          FastAPI 入口
    models.py        统一消息模型 Message
    schemas.py       接口入参/出参
    api/messages.py  消息写入、查询、渠道回调入口
    channels/        各 IM 渠道适配器（wecom / dingtalk / feishu，待实现）
  tests/
web/                 React 工作台（消息收件箱，工单/知识库/积分排名占位）
docker-compose.yml
```

## 统一消息模型

三个渠道的群消息都归一成 `Message`（`backend/app/models.py`）：

| 字段 | 说明 |
| --- | --- |
| `channel` | 渠道：`wecom` / `dingtalk` / `feishu` |
| `external_id` | 渠道侧消息 ID，与 `channel` 联合唯一，用于回调重试去重 |
| `group_id` / `group_name` | 群 ID / 群名 |
| `is_external_group` | 是否外部群（客户群） |
| `sender_id` / `sender_name` | 发送人 ID / 名称 |
| `msg_type` | `text` / `image` / `file` / `other` |
| `content` | 归一后的文本内容 |
| `sent_at` | 渠道侧发送时间 |
| `received_at` | 入库时间 |
| `raw` | 渠道原始报文 |

接入新渠道：在 `backend/app/channels/` 实现 `ChannelAdapter.parse()`，把回调报文转成 `MessageIn`，回调地址为 `POST /webhooks/{channel}`。

## 接口

- `GET /api/health`
- `POST /api/messages`：写入一条已归一的消息
- `GET /api/messages?channel=&group_id=&since=&limit=`：查询消息
- `POST /webhooks/{channel}`：渠道回调（目前返回 501）
- 接口文档：启动后访问 http://localhost:8000/docs

## 本地运行

一键启动：

```bash
docker compose up --build
```

工作台在 http://localhost:5173 ，后端在 http://localhost:8000 。

分别开发：

```bash
# 后端
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload
pytest

# 前端
cd web
npm install
npm run dev
```

## 路线图

- [x] 项目骨架与统一消息模型
- [ ] 企业微信群消息接入
- [ ] 钉钉、飞书群消息接入
- [ ] AI 预处理与回复建议
- [ ] 转技术支持协助
- [ ] 知识库沉淀与自迭代（pgvector）
- [ ] 内部工单
- [ ] 技术支持积分排名
