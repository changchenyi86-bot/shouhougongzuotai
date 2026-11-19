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
    ingest.py        消息入库统一入口（去重 + 触发 AI 建议）
    delivery.py      回复发送方式（目前为复制后人工发送，预留直发接口）
    ai/              AI 回复建议：llm.py 模型接口（可切换）、suggest.py 提示词、worker.py 后台生成
    api/suggestions.py  收件箱、采纳 / 修改 / 转人工 / 忽略 / 重新生成
    channels/        各 IM 渠道适配器（wecom 已实现：会话内容存档；dingtalk / feishu 待实现）
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
- `POST /webhooks/{channel}`：渠道回调（钉钉、飞书目前返回 501）
- `GET/POST /webhooks/wecom`：企业微信会话存档应用的事件回调（URL 校验、msgaudit_notify）
- `GET /api/inbox?view=todo|escalated|done|all&channel=`：收件箱（消息 + AI 建议）
- `POST /api/suggestions/{id}/adopt|escalate|dismiss|regenerate`：处理 AI 建议
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

## 企业微信接入（会话内容存档）

群消息通过企业微信「会话内容存档」获取，内部群和外部客户群都能拿到全部消息：

1. 企业微信管理后台开通会话内容存档（付费功能），记下存档 Secret，配置消息加密公钥并保存对应私钥。
2. 下载官方 Linux C SDK（`libWeWorkFinanceSdk_C.so`），和私钥一起放到项目根目录 `secrets/wecom/`（已在 .gitignore 中），docker-compose 会挂载到容器 `/opt/wecom`。
3. 在 `backend/.env` 填写 `WECOM_CORP_ID`、`WECOM_ARCHIVE_SECRET`、`WECOM_ARCHIVE_PRIVATE_KEY_FILES`（见 `.env.example`）。后端每 30 秒增量拉取一次。
4. 可选：存档应用「接收事件服务器」填 `https://<域名>/webhooks/wecom` 并配置 `WECOM_CALLBACK_TOKEN` / `WECOM_CALLBACK_AES_KEY`，有新消息时会立即拉取。
5. 可选：填写 `WECOM_CONTACT_SECRET`（客户联系 Secret），工作台可显示外部客户群名称。

发送人 ID 以 `wm` / `wo` 开头的是客户，只有客户的文本消息会生成 AI 建议；员工发言也入库，作为上下文。
会话存档不能通过 API 发消息，所以工作台「采纳」后会把回复复制到剪贴板，由技术支持粘贴到企业微信发送（客户群回复保留人工确认）。

## AI 回复建议

新的客户消息入库后，后台结合同群最近 20 条聊天记录生成回复建议，给出「可直接回复 / 建议转人工」和把握程度。
模型可配置（`LLM_PROVIDER`）：

- `claude`（默认）：Anthropic Claude，密钥放在 `ANTHROPIC_API_KEY`
- `openai_compatible`：任何兼容 OpenAI Chat Completions 协议的接口，如公司自有模型网关，配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`

知识库检索的接入点在 `app/ai/suggest.py` 的 `retrieve_knowledge()`。

## 路线图

- [x] 项目骨架与统一消息模型
- [x] 企业微信群消息接入（会话内容存档）
- [ ] 钉钉、飞书群消息接入
- [x] AI 预处理与回复建议（复制后人工发送；直发待定，如经网易七鱼）
- [ ] 转技术支持协助
- [ ] 知识库沉淀与自迭代（pgvector）
- [ ] 内部工单
- [ ] 技术支持积分排名
