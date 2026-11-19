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
    send_queue.py    自动发送队列（确认发送 → 电脑端发送程序领取 → 回报结果）、频率限制
    ai/              AI 回复建议：llm.py 模型接口（可切换）、suggest.py 提示词、worker.py 后台生成
    api/suggestions.py  AI 建议：采纳 / 修改 / 转人工 / 忽略 / 重新生成
    api/conversations.py 会话：群列表、聊天记录、在会话里发文字 / 图片 / 文件
    api/attachments.py  图片、文件上传和下载（客户发来的也会下载保存）
    api/sending.py   自动发送：发送程序心跳 / 领取 / 回报，工作台重发 / 取消 / 改为手动
    channels/        各 IM 渠道适配器（wecom 已实现：会话内容存档；dingtalk / feishu 待实现）
  tests/
windows-agent/       电脑端发送程序：在常开的 Windows 电脑上操作官方企业微信客户端发消息
web/                 React 工作台（会话：群列表 + 聊天记录 + AI 建议 + 输入框；工单/知识库/积分排名占位）
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
- `GET /api/groups?view=todo|escalated|all`：群列表（待处理数、转人工数）
- `GET /api/conversation?channel=&group_id=&before=|after=|anchor=`：一个群的聊天记录（含 AI 建议、附件、工作台发出的消息状态）
- `POST /api/attachments`（表单上传）、`GET /api/attachments/{id}`：图片、文件
- `POST /api/conversations/send`：在会话里发文字 + 图片 + 文件（可带 `suggestion_id` 表示回复哪条客户消息）
- `POST /api/send-tasks/{id}/resend|cancel|mark-manual-sent`：发送失败后重发、取消排队、标记已手动发送
- `POST /api/messages/{id}/fetch-media`：重新下载客户发的图片或文件
- `GET /api/inbox?view=todo|escalated|done|all&channel=`：按消息列出待处理（旧接口，工作台已不用）
- `POST /api/suggestions/{id}/adopt|escalate|dismiss|regenerate`：处理 AI 建议
- `GET /api/send/status`：自动发送程序是否在线
- `POST /api/suggestions/{id}/adopt` 传 `send: true`：确认后交给发送程序自动发到群里
- `POST /api/suggestions/{id}/resend|cancel-send|mark-manual-sent`：自动发送失败后重发、取消排队、标记已手动发送
- `POST /api/agent/heartbeat|claim`、`POST /api/agent/tasks/{id}/result`：发送程序专用，需 `Authorization: Bearer <SEND_AGENT_TOKEN>`
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

# 只看界面（内置演示数据，不需要后端和数据库），浏览器打开 http://localhost:5180/demo.html
npm run demo
```

## 企业微信接入（会话内容存档）

群消息通过企业微信「会话内容存档」获取，内部群和外部客户群都能拿到全部消息：

1. 企业微信管理后台开通会话内容存档（付费功能），记下存档 Secret，配置消息加密公钥并保存对应私钥。
2. 下载官方 Linux C SDK（`libWeWorkFinanceSdk_C.so`），和私钥一起放到项目根目录 `secrets/wecom/`（已在 .gitignore 中），docker-compose 会挂载到容器 `/opt/wecom`。
3. 在 `backend/.env` 填写 `WECOM_CORP_ID`、`WECOM_ARCHIVE_SECRET`、`WECOM_ARCHIVE_PRIVATE_KEY_FILES`（见 `.env.example`）。后端每 30 秒增量拉取一次。
4. 可选：存档应用「接收事件服务器」填 `https://<域名>/webhooks/wecom` 并配置 `WECOM_CALLBACK_TOKEN` / `WECOM_CALLBACK_AES_KEY`，有新消息时会立即拉取。
5. 可选：填写 `WECOM_CONTACT_SECRET`（客户联系 Secret），工作台可显示外部客户群名称。

发送人 ID 以 `wm` / `wo` 开头的是客户，只有客户的文本消息会生成 AI 建议；员工发言也入库，作为上下文。
群里的图片和文件会在后台下载，保存在 `ATTACHMENT_DIR`（docker-compose 里是 `attachments` 卷），单个上限 `ATTACHMENT_MAX_MB`（默认 20MB），下载失败可在工作台重试。
会话存档不能通过 API 发消息，所以回复有两种发法（客户群回复都保留人工确认）：

- 复制后手动发送：工作台把回复复制到剪贴板，技术支持粘贴到企业微信发送。
- 自动发送（原型）：一台常开的 Windows 电脑登录专用客服企业微信账号，运行 `windows-agent/` 里的发送程序。
  技术支持在工作台「会话」里写好回复（可加图片、文件）并确认后，发送程序操作官方企业微信客户端：搜索群名、点开、
  截屏识字核对群名、通过剪贴板粘贴（图片、文件、文字依次）、发送，再把成功或失败回报给工作台。详见 [windows-agent/使用说明.md](windows-agent/使用说明.md)。

自动发送的安全措施：只通过操作客户端界面发送，不碰通信协议、不注入客户端；群名未知或有同名群时不发；
发送前后三次核对（标题是目标群、内容在输入框里、发送后输入框已清空）；两次发送至少间隔 5 秒、每群每小时最多 20 条；
排队超过 10 分钟自动取消；发送中断时标记「结果未知」，不自动重发。

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
- [x] AI 预处理与回复建议
- [x] 企业微信自动发送原型（电脑端发送程序操作客户端，待真机验证）
- [ ] 转技术支持协助
- [ ] 知识库沉淀与自迭代（pgvector）
- [ ] 内部工单
- [ ] 技术支持积分排名
