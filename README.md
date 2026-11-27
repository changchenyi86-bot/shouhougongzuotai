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
    ai/              AI 回复建议：llm.py 模型接口（可切换）、suggest.py 提示词、knowledge.py 查售后数字人知识、worker.py 后台生成；
                     similar.py 相似历史问答（向量检索）、refine.py 和 AI 对话润色、reflow.py 采纳情况与回流判断
    api/suggestions.py  AI 建议：采纳 / 修改 / 转人工 / 忽略 / 重新生成 / 改回待回复
    api/assist.py    和 AI 对话润色回复
    api/candidates.py   候选知识：确认 / 审核 / 停用，以及给同步脚本用的纠错卡
    api/stats.py     AI 建议采纳情况（按人按周）
    ai/evaluate.py、api/evaluation.py  回复建议评测：导入题库、后台一键跑一轮、打分、按块统计、和上一轮对比
    cards.py         审核通过的知识转成售后数字人的 BI 纠错卡
    api/conversations.py 会话：群列表、聊天记录、在会话里发文字 / 图片 / 文件
    api/attachments.py  图片、文件上传和下载（客户发来的也会下载保存）
    api/sending.py   自动发送：发送程序心跳 / 领取 / 回报，工作台重发 / 取消 / 改为手动
    auth.py、api/staff.py  登录（账号密码）、成员、角色（管理员 / 技术支持）、分组（BI 组 / 中台组）
    routing.py       会话分流（群负责人 → 同组在线、手上会话最少的人 → 都不在线还给负责人）和可见范围
    customers.py、api/customers.py  客户档案：从售后系统、运维门户只读同步客户资料和服务记录，群对应客户，团队补充，带给 AI
    channels/        各 IM 渠道适配器（wecom 已实现：会话内容存档；dingtalk / feishu 待实现）
  tests/
scripts/sync_digital_human_cards.py  把审核通过的知识写进售后数字人的 BI 纠错卡库（在能 SSH 到数字人服务器的电脑上运行）
windows-agent/       电脑端发送程序：在常开的 Windows 电脑上操作官方企业微信客户端发消息
web/                 React 工作台（登录；会话：群列表 + 聊天记录 + AI 建议 + 和 AI 对话 + 输入框 + 右边的客户档案；知识库：候选知识确认和审核；
                     采纳统计；评测；团队设置：成员、群负责人；工单/积分排名占位）
docker-compose.yml
```

## 会话分流

群里的一次会话：客户发来要处理的消息、这个群又没有进行中的会话时开始，技术支持结束会话时结束。
新会话先给群负责人；负责人不在线（设成离开，或 2 分钟内没打开工作台），给同组里在线、手上会话最少的人；
同组都不在线，还是给负责人，负责人打开工作台时提醒。没有负责人的群进「待分配」，由管理员分。
群负责人在「团队设置 → 群负责人」里指定，群属于负责人所在的组。

## 统一消息模型

三个渠道的群消息都归一成 `Message`（`backend/app/models.py`）：

| 字段 | 说明 |
| --- | --- |
| `channel` | 渠道：`wecom` / `dingtalk` / `feishu` |
| `external_id` | 渠道侧消息 ID，与 `channel` 联合唯一，用于回调重试去重 |
| `group_id` / `group_name` | 群 ID / 群名 |
| `is_external_group` | 是否外部群（客户群） |
| `sender_id` / `sender_name` | 发送人 ID / 群里显示的名字 |
| `msg_type` | `text` / `image` / `file` / `other` |
| `content` | 归一后的文本内容 |
| `sent_at` | 渠道侧发送时间 |
| `received_at` | 入库时间 |
| `raw` | 渠道原始报文 |

接入新渠道：在 `backend/app/channels/` 实现 `ChannelAdapter.parse()`，把回调报文转成 `MessageIn`，回调地址为 `POST /webhooks/{channel}`。

## 接口

除了健康检查、登录、渠道回调和发送程序专用接口，其余接口都要先登录（浏览器 Cookie）。处理人一律记登录的人。

- `GET /api/health`
- `GET /api/auth/state`：是否还没有任何账号（第一次使用要先设管理员）
- `POST /api/auth/setup`：第一次使用时设管理员账号；`POST /api/auth/login|logout`、`GET /api/auth/me`
- `PUT /api/auth/me/status`（在线 / 离开）、`PUT /api/auth/me/password`
- `GET /api/teams`、`GET /api/staff`：分组、成员；`POST /api/staff`、`PATCH /api/staff/{id}`：添加、修改成员（管理员）
- `POST /api/messages`：写入一条已归一的消息
- `GET /api/messages?channel=&group_id=&since=&limit=`：查询消息
- `POST /webhooks/{channel}`：渠道回调（钉钉、飞书目前返回 501）
- `GET/POST /webhooks/wecom`：企业微信会话存档应用的事件回调（URL 校验、msgaudit_notify）
- `GET /api/groups?view=todo|escalated|unassigned|all`：群列表（待处理数、转人工数、负责人、谁在处理）。管理员看全部，其他人只看自己负责的群和正在替别人处理的群；unassigned 是还没分给人的会话
- `PUT /api/groups/owner`：指定或取消群负责人（管理员）
- `POST /api/conversations/assign`：把进行中的会话转给同事（管理员、群负责人、正在处理的人）
- `GET /api/conversation?channel=&group_id=&before=|after=|anchor=`：一个群的聊天记录（含 AI 建议、附件、工作台发出的消息状态）
- `POST /api/attachments`（表单上传）、`GET /api/attachments/{id}`：图片、文件
- `GET /api/conversation/members?channel=&group_id=`：群里能 @ 的人（输入框打 @ 时选）
- `POST /api/conversations/send`：在会话里发文字 + 图片 + 文件（可带 `suggestion_id` 表示回复哪条客户消息，`mentions` 表示文字里要真正 @ 的人）
- `POST /api/send-tasks/{id}/resend|cancel|mark-manual-sent`：发送失败后重发、取消排队、标记已手动发送
- `POST /api/conversations/end`：结束会话（群里没回复的客户消息不再算待处理，没发出去的回复不再发送；客户再发消息时重新回到待处理），记录是谁结束的
- `POST /api/messages/{id}/fetch-media`：重新下载客户发的图片或文件
- `GET /api/inbox?view=todo|escalated|done|all&channel=`：按消息列出待处理（旧接口，工作台已不用）
- `POST /api/suggestions/{id}/adopt|escalate|dismiss|regenerate|reopen`：处理 AI 建议（reopen：把「无需回复」改回待回复）
- `GET/POST /api/suggestions/{id}/chat`：和 AI 对话润色回复（技术支持说方案或要点，AI 写成发给客户的回复）
- `GET /api/candidates?view=proposed|pending|approved|rejected|skipped`、`GET /api/candidates/counts`：候选知识
- `POST /api/candidates/{id}/confirm|decline|approve|reject|retire`：确认加入候选知识 / 不用 / 审核通过 / 不通过 / 停用
- `GET /api/digital-human/cards`、`POST /api/digital-human/cards/synced`：同步脚本取要写入售后数字人的纠错卡、回报写入结果
- `GET /api/stats/adoption?weeks=8`：AI 建议采纳情况，按人按周
- `GET /api/eval/sets`、`POST /api/eval/sets/{name}/import`（管理员，上传 .jsonl）：评测题库
- `GET /api/eval/runs`、`POST /api/eval/runs`（管理员）、`POST /api/eval/runs/{id}/stop`（管理员）：评测轮次
- `GET /api/eval/runs/{id}`、`GET /api/eval/runs/{id}/results`：一轮的分块统计、和上一轮对比、逐题结果
- `GET /api/customers/card?channel=&group_id=`：一个群的客户档案（对应哪家客户、同步来的资料、只给内部看的提醒、团队补充、最近服务记录、同步状态）
- `GET /api/customers/records?channel=&group_id=&offset=`、`GET /api/customers/search?q=`：这个群的全部服务记录；按简称或签约公司找客户
- `PUT /api/customers/link`：手动指定群对应的客户（管理员、群负责人；同步不覆盖）
- `POST /api/customers/notes`、`PATCH|DELETE /api/customers/notes/{id}`：团队补充、资料有误（能看这个群的人都能写；自己写的自己改，管理员都能改）
- `POST /api/customers/sync`：马上同步一次客户档案（管理员）
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

工作台在 http://localhost:5173 ，后端在 http://localhost:8000 。第一次打开工作台时先设管理员账号，再在「团队设置」里给大家开账号。
部署到 https 后在 `backend/.env` 设 `COOKIE_SECURE=true`。

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
3. 在 `backend/.env` 填写 `WECOM_CORP_ID`、`WECOM_ARCHIVE_SECRET`、`WECOM_ARCHIVE_PRIVATE_KEY_FILES`（见 `.env.example`）。后端每 3 秒增量拉取一次（`WECOM_ARCHIVE_POLL_INTERVAL`，企业微信限制拉取接口每分钟 4000 次，3 秒一次远低于上限）。新消息入库后工作台页面 2 秒内刷新，AI 建议在后台另外生成，不耽误消息显示。
4. 可选：存档应用「接收事件服务器」填 `https://<域名>/webhooks/wecom` 并配置 `WECOM_CALLBACK_TOKEN` / `WECOM_CALLBACK_AES_KEY`，有新消息时会立即拉取。
5. 可选：填写 `WECOM_CONTACT_SECRET`（客户联系 Secret），工作台可显示外部客户群名称，以及群里每个人的昵称（群昵称优先）。新开通的企业没有单独的客户联系 Secret，用「客户联系 → 客户 → API → 可调用接口的应用」里那个自建应用的 Secret。
   自建应用的「企业可信IP」不收第三方服务商的 IP；本机出口 IP 加不进去时，经一台已在可信 IP 里的服务器转发：
   本机开 `ssh -N -D 127.0.0.1:18968 <服务器>`，`.env` 里填 `WECOM_CONTACT_PROXY=socks5://host.docker.internal:18968`（只有查群名和群成员走它）。
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

发送人 ID 以 `wm`（微信用户）、`wo` / `lo`（其他企业的企业微信用户）开头的是客户，只有客户的文本消息会生成 AI 建议；员工发言也入库，作为上下文。
聊天记录里每条消息显示发送人的昵称和来源（灰色「内」是本企业员工，绿色「@微信」、橙色「@企业微信」是客户）。昵称来自外部群的成员名单，
需要上面的客户联系 Secret；内部群的接口不给名字，显示员工账号；查不到昵称的客户显示「客户 ·ID 末尾 4 位」。
以前入库、没有名字的消息，后台每 10 分钟补一次。
群里的图片和文件会在后台下载，保存在 `ATTACHMENT_DIR`（docker-compose 里是 `attachments` 卷），单个上限 `ATTACHMENT_MAX_MB`（默认 20MB），下载失败可在工作台重试。
会话存档不能通过 API 发消息，所以回复有两种发法（客户群回复都保留人工确认）：

- 复制后手动发送：工作台把回复复制到剪贴板，技术支持粘贴到企业微信发送。
- 自动发送（原型）：一台常开的 Windows 电脑登录专用客服企业微信账号，运行 `windows-agent/` 里的发送程序。
  技术支持在工作台「会话」里写好回复（可加图片、文件）并确认后，发送程序操作官方企业微信客户端：搜索群名、点开、
  截屏识字核对群名、通过剪贴板粘贴（图片、文件、文字依次）、发送，再把成功或失败回报给工作台。详见 [windows-agent/使用说明.md](windows-agent/使用说明.md)。

回复里可以 @ 群成员：输入框打 @ 弹出群成员（外部群用群成员名单，最近说过话的在前，发送电脑上的客服账号自己不列），
用 AI 建议时开头自动 @ 提问的客户（比较采纳情况时不算改动）。发送程序在企业微信里像人一样打 @、输入名字、
在弹出的成员列表里识字点中这个人，对方收到的是真正的 @ 提醒；同名的按来源标签（员工、@微信、@公司名）区分，
分不清、找不到、列表没弹出时按普通文字发出，并在工作台提示谁没 @ 上。内部员工在客服账号眼里显示的可能是内部姓名，
和外部群名单里的名字不一样，这时也会按普通文字发出。

自动发送的安全措施：只通过操作客户端界面发送，不碰通信协议、不注入客户端；群名未知或有同名群时不发；
发送前后三次核对（标题是目标群、内容在输入框里、发送后输入框已清空）；两次发送至少间隔 5 秒、每群每小时最多 20 条；
排队超过 10 分钟自动取消；发送中断时标记「结果未知」，不自动重发。

## AI 回复建议

新的客户消息入库后，后台结合同群最近 20 条聊天记录生成回复建议，给出「可直接回复 / 建议转人工」和把握程度。
AI 同时判断这条消息需不需要回复：寒暄、致谢、确认收到、测试这类，不超过 `AUTO_DISMISS_MAX_CHARS` 个字、不带问号、也没有 @ 人的，
自动标成「AI 判断无需回复」，不算待处理，聊天记录里可以一键改回待回复；其余只在建议上提示「AI 觉得可能不用回复」。
后台重启时还在生成中的建议，启动后自动补生成（太久以前的消息标为生成失败，可以手动重新生成）。
模型可配置（`LLM_PROVIDER`）：

- `claude`（默认）：Anthropic Claude，密钥放在 `ANTHROPIC_API_KEY`
- `openai_compatible`：任何兼容 OpenAI Chat Completions 协议的接口，如公司自有模型网关，配置 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`

客户图片和文件下载成功后会在后台识别一次，生成建议时最多等待 `MEDIA_WAIT_SECONDS` 秒，识别结果以文字加入聊天上下文；识别不成功不影响建议生成。`MEDIA_EXTRACT_ENABLED=false` 可关闭识别，默认识图使用 `LLM_MODEL`（公司网关为 `deepseek-flash`）；`VISION_MODEL` 可换成网关里其他能看图的模型，`VISION_BASE_URL` / `VISION_API_KEY` 可指定独立接口。客户截图可能含账号、内网地址，不要配置境外模型。上线前已下载的附件用 `python -m app.extract_media --since <日期>` 补识别。

### 参考售后数字人的知识

生成建议前，工作台先到售后数字人（自建实例，不接生产）的检索服务查这条客户问题的参考知识，
数字人是知识的唯一来源，工作台不另存一份。在 `backend/.env` 配置 `KNOWLEDGE_MCP_URL`（检索服务的 `/messages` 地址），留空表示不查。

- 查什么：BI 纠错卡（技术支持对常答错问题的更正，覆盖度够才用）；产品手册、FAQ、历史工单按「限定手册、限定 FAQ、不限类型」三路检索，
  合并去重后取前 `KNOWLEDGE_TOP` 篇，前 `KNOWLEDGE_READ_FULL` 篇把全文带给模型，其余只带摘要。和知数里售后数字人技能的查法一致。
- 每条标出适用分支（有数 / 知数）。`KNOWLEDGE_PRODUCT=bi` 查有数，检索服务会自动带上知数。
- 模型在建议里列出实际依据的条目，工作台在建议下方显示「依据知识 N 条」，可点开原文；纠错卡还是草稿的会标出来。
- 寒暄、确认类消息（好的、谢谢、收到）不查。检索服务连不上时照常生成建议，只是没有参考资料，并在建议下方提示原因。
- 建议没有依据任何知识时（没查、没查到、查到的都不相关）在建议下方明确标出，提醒发送前核对。
- 检索服务没有登录验证、返回内容含客户工单，只经本机隧道访问，不要对外开放。

接入点在 `app/ai/suggest.py` 的 `retrieve_knowledge()`，检索逻辑在 `app/ai/knowledge.py`。

### 相似历史问答

配好 embedding 后，工作台把每条发出的回复（客户问题 + 技术支持实际发出的回复）存成向量（pgvector），
生成建议时按客户消息查最相似的几条一起交给模型（`SIMILAR_QA_TOP`、相似度下限 `SIMILAR_QA_MIN_SCORE`）：

- 工作台知识：候选知识审核通过的问答，提示词里编号 W1、W2，可信度和纠错卡相当。
- 历史回复：技术支持以前发出的回复，未经审核，编号 H1、H2，只参考思路和口径，不照搬客户特有信息。

寒暄、确认类消息不存也不查。向量模型连不上时照常生成建议，下次启动时补存这期间的回复。检索在 `app/ai/similar.py`。

### 和 AI 对话润色

AI 建议下方点「和 AI 对话」，技术支持说自己的方案、要点或修改意见，AI 写成可以直接发给客户的回复，可以多轮改；
满意后点「填入输入框」，走原来的确认发送。对话只有技术支持能看到，记在 `suggestion_chats` 表。

### 采纳情况和回流（知识自迭代）

回复发出时，工作台把实际发出的内容和 AI 原建议比较，记为原样采纳 / 小改 / 大改 / 未用，以及是否经 AI 对话润色，
「采纳统计」页按人按周看（以后积分排名也用这些数据）。技术支持自己打字回复时，输入框自动关联正在处理的客户消息，发出后记为已回复。

没有原样采纳的回复（改过 AI 建议、经对话润色、自己写的），由 AI 判断是不是通用问题：闲聊、只针对这个客户的个案、
只改了语气称呼的不沉淀。是通用问题就整理成通用问答（问题、通用答案、适用分支、类型），流程是：

1. 回复的人在会话里（或「知识库 → 待我确认」）确认加入候选知识，可以先改；
2. 其他技术支持在「知识库 → 待审核」对照客户实际收到的原话审核，不能审核自己回复或提交的；
3. 审核通过后，工作台生成建议时马上能用上；有数和通用的知识转成售后数字人的 BI 纠错卡（编号 `bi-verified-wb-` 开头，
   状态 confirmed，审核人即工作台审核人），由 `scripts/sync_digital_human_cards.py` 写进数字人自建实例的卡库。
   知数的结论卡暂不支持，只在工作台里用。通过后发现答案有误可以停用，同步后数字人那边的卡也停用。

同步脚本读服务器上数字人正在用的卡库，替换其中工作台的卡片、其他卡片原样保留，存成新版本上传，数字人自动重新加载。
数字人那边以后发布了更高版本的卡库，再运行一次脚本即可把工作台的卡合进去。服务器和目录配在 `backend/.env`（见 `.env.example`）。

### 回复建议评测

「评测」页拿一批固定的题，在后台直接走一遍生成建议的流程（查售后数字人、相似问答、模型起草），再打分。
题目不入消息表、不生成建议记录、不发到任何群、不算进采纳统计。用来对比改提示词、换模型、补知识前后的效果。

- 题库：管理员在评测页「导入题目」，上传评测集文件（.jsonl，一行一题）。同编号的题覆盖，文件里没有了的题停用。
  评测集由客户问题整理而来（已脱敏），放在本机项目目录，不进代码仓库。每题的字段：`id`、`kind`（题型）、`group`（分块）、
  `question`、`context`（提问前的群聊，每行「客户：…」或「技术支持：…」）、`expected`（期望答案要点）、`must_not`、
  `reference`（期望要点为空时作参照的回答）、`expected_keys`（依据文档或纠错卡编号）、`expect_reply`（是否需要回复题）。
  题目本身有问题的（缺截图、参考答案矛盾等）标 `status: "待复核"` 并写 `review_reason`：照样导入但不参与评测、不算分，
  评测页显示待复核的题数；技术支持改好后把 `status` 改回「可用」重新导入。
- 打分：带依据编号的题看检索有没有查到、排第几；「是否需要回复」题看 AI 的判断；其余由模型对照期望要点判
  对 / 部分对 / 错 / 没答上。知识库没有可靠答案的题，如实说明、追问或转人工算对，编造算错。
- 分块出分：「有数 + 知数」「数据中台」「通用」分开算。数据中台的知识还没接进售后数字人自建实例时，那块单独看。
- 一次只跑一轮，同时跑 `EVAL_WORKERS` 道题（默认 2）。每道题调两次模型（起草 + 打分），几百道题一轮约半小时，按用量计费。
  后台重启时没跑完的一轮会接着跑，已跑完的题不重跑。

### 客户档案

会话页右边显示这个群对应客户的档案，AI 写建议时也会带上（只用于判断，不写进回复）：

- 群对应哪家客户：售后系统的群绑定为主（企业微信群 id 对得上就自动对上），任务表库的企微群对照为辅；对不上或对错了，
  群负责人和管理员可以手动选，手动选的同步不会覆盖
- 签约公司、等级、分组、销售、已购产品（售后系统客户增购表）；按有数BI、数据中台两条线的维保状态和到期日（售后系统维保总览）、商务状态
- 环境：各产品的生产、测试等环境和版本、集群类型、负责的技术支持和运维（运维门户「客户环境」、售后系统环境表）
- 最近服务记录：售后系统里按群记的服务小结（七鱼时代起），按更新时间增量同步
- 团队补充、资料有误：写在工作台里，跟着客户走（这家的几个群都看得到）；同步来的资料在工作台里只读，不对就报资料有误，管理员去源头改
- 这个群对应产品线的维保过期或流失时，AI 建议下方提醒「仅内部」，不会写进回复

在 `backend/.env` 配 `CUSTOMER_DB_CONFIG_FILE`（数据库连接文件，JSON：host、port、username、password）后，后台每
`CUSTOMER_SYNC_MINUTES` 分钟（默认 60）只读同步一次：只读事务、只查固定的表和列；运维门户环境表里的账号密码、访问地址、
服务器信息，以及金额、客户联系人一律不取。连接文件放在本机，用 docker-compose.override.yml 只读挂进容器，不进仓库。

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
- [x] 客户档案：群对应客户、签约公司、维保、环境版本、服务记录、团队补充，AI 建议参考档案
- [ ] 转技术支持协助
- [x] 回复建议参考售后数字人的知识（纠错卡、手册、FAQ、工单），并标出引用
- [x] 知识库沉淀与自迭代：AI 判断通用问题 → 回复的人确认 → 工作台审核 → 工作台马上用上，经 BI 纠错卡写入售后数字人
- [x] 和 AI 对话润色回复、AI 判断无需回复、相似历史问答、采纳情况统计
- [ ] 内部工单
- [ ] 技术支持积分排名
