import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # APP_ENV_FILE 设为空字符串时不读 .env（测试用，避免本机配置影响测试结果）
    model_config = SettingsConfigDict(env_file=os.environ.get("APP_ENV_FILE", ".env") or None, extra="ignore")

    database_url: str = "postgresql+psycopg://support:support@localhost:5432/support"
    cors_origins: list[str] = ["http://localhost:5173"]

    # ---- AI 回复建议 ----
    ai_suggest_enabled: bool = True
    # 模型来源：claude（Anthropic，密钥读 ANTHROPIC_API_KEY）或 openai_compatible（自有模型网关）
    llm_provider: str = "claude"
    claude_model: str = "claude-opus-5-5"
    claude_effort: str = "medium"
    llm_base_url: str = ""  # 如 https://llm.example.com/v1
    llm_api_key: str = ""
    llm_model: str = ""
    llm_json_mode: bool = True  # 接口不支持 response_format 时设为 false
    llm_timeout: float = 60
    ai_context_messages: int = 20  # 生成建议时带上的群内最近消息条数
    ai_workers: int = 4
    ai_suggest_max_age_minutes: int = 120  # 超过这个时间的旧消息（如首次导入的历史记录）不生成建议

    # ---- 售后数字人知识检索（自建实例的检索服务，MCP 接口） ----
    # 如 http://host.docker.internal:<本机端口>/messages，留空表示生成建议时不查知识库
    knowledge_mcp_url: str = ""
    knowledge_product: str = "bi"  # 查哪个产品的知识：bi（有数，检索服务会自动带上知数）、zs（只查知数）
    knowledge_top: int = 6  # 带给模型的手册、FAQ、工单篇数（纠错卡另算，最多 3 张）
    knowledge_read_full: int = 3  # 前几篇把全文带给模型，其余只带摘要
    knowledge_article_chars: int = 1500  # 每篇全文最多带多少字
    knowledge_timeout: float = 15

    # ---- 知识库向量检索（embedding，OpenAI 兼容的 /v1/embeddings 接口） ----
    embedding_base_url: str = ""  # 如 http://host.docker.internal:<端口>/v1，留空表示未配置
    embedding_api_key: str = ""  # 接口不需要鉴权时留空
    embedding_model: str = ""
    embedding_dim: int = 1024  # 向量维度，pgvector 列按这个长度建；换模型后维度变了要重建向量
    embedding_timeout: float = 10

    # ---- 相似历史问答（工作台自己的向量检索：技术支持发出过的回复、审核通过的工作台知识） ----
    similar_qa_top: int = 3  # 两类各最多带几条给模型
    # 相似度下限（余弦）。bge-m3 实测同义问法 0.67～0.92、无关句 0.42～0.46；上线后用真实问答再校准
    similar_qa_min_score: float = 0.72

    # ---- 回复发出后的回流：AI 判断是否通用问题，客服确认后进候选知识，审核通过才写入 ----
    reflow_enabled: bool = True
    # 同步脚本（scripts/sync_digital_human_cards.py）取纠错卡用的口令，留空表示不能同步
    cards_sync_token: str = ""
    # 自动判为「无需回复」的消息最长多少字（去掉标点、@ 后），更长的只提示、不自动标记，避免漏回客户
    auto_dismiss_max_chars: int = 40

    # ---- 回复建议评测（评测页一键跑题库，不发到群里）：一轮里同时跑几道题，每道题调两次模型（起草 + 打分） ----
    eval_workers: int = 2

    # ---- 企业微信 会话内容存档 ----
    wecom_corp_id: str = ""
    wecom_archive_secret: str = ""  # 「会话内容存档」应用的 Secret
    # 消息加密私钥，按 publickey_ver 映射到 PEM 文件路径，JSON 格式：{"2": "/opt/wecom/archive_v2.pem"}
    # 更换公钥后企业微信的版本号加 1，新旧版本的私钥都可以配在这里
    wecom_archive_private_key_files: dict[str, str] = {}
    # 没有私钥、直接跳过的公钥版本，JSON 格式：["1"]。
    # 用于明知解不开的旧消息（如旧版本私钥只在七鱼手里）；没配私钥也没列在这里的版本会停止拉取，避免悄悄丢消息
    wecom_archive_skip_key_versions: list[str] = []
    wecom_sdk_lib_path: str = "/opt/wecom/libWeWorkFinanceSdk_C.so"
    wecom_archive_proxy: str = ""
    wecom_archive_proxy_password: str = ""
    wecom_archive_batch_size: int = 100
    # 秒，0 表示只靠回调通知触发拉取。企业微信限制拉取接口每分钟 4000 次，3 秒一次（每分钟 20 次）远低于上限
    wecom_archive_poll_interval: float = 3
    # 存档应用「接收事件服务器」配置，收到 msgaudit_notify 时立即拉取
    wecom_callback_token: str = ""
    wecom_callback_aes_key: str = ""
    # 「客户联系」Secret，可选：用于查询外部客户群名称。新企业没有单独的客户联系 Secret 时，
    # 用「客户联系 → 客户 → API → 可调用接口的应用」里那个自建应用的 Secret
    wecom_contact_secret: str = ""
    # 查外部客户群名称走的代理，如 socks5://host.docker.internal:18968（本机 ssh -D 经有可信 IP 的服务器出网）。
    # 自建应用的企业可信 IP 不收第三方服务商的 IP，本机出口 IP 加不进去时用；留空表示直连
    wecom_contact_proxy: str = ""

    # ---- 登录 ----
    login_days: int = 30  # 登录一次保持多少天
    cookie_secure: bool = False  # 部署到 https 后设为 true，Cookie 只走加密连接
    staff_online_seconds: int = 120  # 超过这个时间没打开工作台，视为不在线

    # ---- 附件（图片、文件） ----
    attachment_dir: str = "./data/attachments"
    attachment_max_mb: int = 20  # 上传和下载的单个文件上限

    # ---- 自动发送（电脑端发送程序操作企业微信客户端） ----
    send_agent_token: str = ""  # 发送程序的访问口令，留空表示不启用自动发送
    send_min_interval_seconds: float = 5  # 两次发送之间至少间隔
    send_group_max_per_hour: int = 20  # 每个群每小时最多自动发送条数
    send_queue_timeout_minutes: int = 10  # 排队超过这个时间仍未被领取则取消，避免很久以后才发出过时的回复
    send_result_timeout_minutes: int = 3  # 发送中超过这个时间没回报结果，标记为结果未知
    send_agent_offline_seconds: int = 60  # 超过这个时间没有心跳视为离线

    @field_validator("wecom_archive_skip_key_versions", mode="before")
    @classmethod
    def _versions_as_str(cls, v):
        # 允许写成 [1] 或 ["1"]
        return [str(x) for x in v] if isinstance(v, (list, tuple, set)) else v

    @property
    def wecom_archive_enabled(self) -> bool:
        return bool(self.wecom_corp_id and self.wecom_archive_secret and self.wecom_archive_private_key_files)


settings = Settings()
