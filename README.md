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
    ai/              AI 回复建议：llm.py 模型接口（可切换）、suggest.py 提示词、knowledge.py 查售后数字人知识、worker.py 后台生成
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
- `POST /api/conversations/end`：结束会话（群里没回复的客户消息不再算待处理，没发出去的回复不再发送；客户再发消息时重新回到待处理），记录是谁结束的
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
docker compose up -d --build
```

三个服务都设置了自动重启：只要 Docker 在运行，电脑重启后工作台会自己起来。
Mac 上不想装 Docker Desktop 的话，可以用 Homebrew 装 Colima：`brew install colima docker docker-compose`，再用 `brew services start colima` 设为开机自启。

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
5. 可选：填写 `WECOM_CONTACT_SECRET`（客户联系 Secret），工作台可显示外部客户群名称。新开通的企业没有单独的客户联系 Secret，用「客户联系 → 客户 → API → 可调用接口的应用」里那个自建应用的 Secret。
   自建应用的「企业可信IP」不收第三方服务商的 IP；本机出口 IP 加不进去时，经一台已在可信 IP 里的服务器转发：
   本机开 `ssh -N -D 127.0.0.1:18968 <服务器>`，`.env` 里填 `WECOM_CONTACT_PROXY=socks5://host.docker.internal:18968`（只有查群名走它）。
   还是查不到群名的群，技术支持可以在「会话」页群名旁点「填写群名」手动填，要和企业微信里显示的群名一字不差，自动发送靠它找群。

### 消息加密密钥（和七鱼共用）

企业微信用「消息加密公钥」加密存档消息，每条消息带 `publickey_ver`（公钥版本号），每更换一次公钥版本号加 1。
版本 1 的公钥是网易七鱼生成的，私钥只在七鱼手里，所以工作台自己生成一对新密钥，公钥上传到企业微信成为新版本，
私钥同时交给七鱼（七鱼后台「会话存档设置 → 保留已有消息加密公钥，设置解密私钥」），两边都能解新消息。

- 生成（RSA 2048，私钥 PKCS#1，即 `BEGIN RSA PRIVATE KEY`）：
  `openssl genrsa -traditional -out secrets/wecom/archive_v2.pem 2048`（OpenSSL 1.x / LibreSSL 去掉 `-traditional`），
  `openssl rsa -in secrets/wecom/archive_v2.pem -pubout -out secrets/wecom/archive_v2_public.pem`。
- 顺序：先在企业微信管理后台上传公钥，再马上把私钥填进七鱼，中间尽量不超过一分钟，选群里没人说话的时候。
  反过来先改七鱼的话，企业微信还在用旧公钥加密，七鱼换了私钥后可能解不开这段时间的消息。
- `backend/.env`：`WECOM_ARCHIVE_PRIVATE_KEY_FILES={"2": "/opt/wecom/archive_v2.pem"}`，
  `WECOM_ARCHIVE_SKIP_KEY_VERSIONS=["1"]`（版本 1 的旧消息工作台解不开，直接跳过）。
  上传后后台显示的版本号不是 2 的话按实际版本号改。没配私钥、也没列在跳过里的版本会让拉取停住并在日志里提示，不会悄悄丢消息。
- 不要在企业微信后台重置会话存档 Secret，七鱼也在用它。

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

### 参考售后数字人的知识

生成建议前，工作台先到售后数字人（自建实例，不接生产）的检索服务查这条客户问题的参考知识，
数字人是知识的唯一来源，工作台不另存一份。在 `backend/.env` 配置 `KNOWLEDGE_MCP_URL`（检索服务的 `/messages` 地址），留空表示不查。

- 查什么：BI 纠错卡（技术支持对常答错问题的更正，覆盖度够才用）；产品手册、FAQ、历史工单按「限定手册、限定 FAQ、不限类型」三路检索，
  合并去重后取前 `KNOWLEDGE_TOP` 篇，前 `KNOWLEDGE_READ_FULL` 篇把全文带给模型，其余只带摘要。和知数里售后数字人技能的查法一致。
- 每条标出适用分支（有数 / 知数）。`KNOWLEDGE_PRODUCT=bi` 查有数，检索服务会自动带上知数。
- 模型在建议里列出实际依据的条目，工作台在建议下方显示「依据售后数字人知识 N 条」，可点开原文；纠错卡还是草稿的会标出来。
- 寒暄、确认类消息（好的、谢谢、收到）不查。检索服务连不上时照常生成建议，只是没有参考资料，并在建议下方提示原因。
- 检索服务没有登录验证、返回内容含客户工单，只经本机隧道访问，不要对外开放。

接入点在 `app/ai/suggest.py` 的 `retrieve_knowledge()`，检索逻辑在 `app/ai/knowledge.py`。以后可以在这里再加一路「相似的历史问答」。

### 知识库向量模型（embedding）

使用 OpenAI 兼容的 `/v1/embeddings` 接口，在 `backend/.env` 配置 `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM`（见 `.env.example`）。
服务只在公司内网时，本机用 SSH 隧道把它转到 127.0.0.1 的某个端口，后端容器通过 `host.docker.internal` 访问该端口；
隧道断了（电脑重启、网络切换）向量检索就不可用，重新打开隧道即可。具体服务器和端口记在本机配置里，不写进仓库。

换模型或换部署后，向量维度和相似度分布可能变化：维度变了要重建已存的向量，知识库「命中」阈值要用实际问答数据重新校准。

## 路线图

- [x] 项目骨架与统一消息模型
- [x] 企业微信群消息接入（会话内容存档）
- [ ] 钉钉、飞书群消息接入
- [x] AI 预处理与回复建议
- [x] 企业微信自动发送原型（电脑端发送程序操作客户端；发文字、图片、文件已在一台真机上测通，和工作台的整条流程待联调）
- [ ] 转技术支持协助
- [x] 回复建议参考售后数字人的知识（纠错卡、手册、FAQ、工单），并标出引用
- [ ] 知识库沉淀与自迭代（技术支持的答案经 BI 纠错卡回写到售后数字人）
- [ ] 内部工单
- [ ] 技术支持积分排名
