from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    # ---- 企业微信 会话内容存档 ----
    wecom_corp_id: str = ""
    wecom_archive_secret: str = ""  # 「会话内容存档」应用的 Secret
    # 消息加密私钥，按 publickey_ver 映射到 PEM 文件路径，JSON 格式：{"1": "/run/secrets/wecom_v1.pem"}
    wecom_archive_private_key_files: dict[str, str] = {}
    wecom_sdk_lib_path: str = "/opt/wecom/libWeWorkFinanceSdk_C.so"
    wecom_archive_proxy: str = ""
    wecom_archive_proxy_password: str = ""
    wecom_archive_batch_size: int = 100
    wecom_archive_poll_interval: int = 30  # 秒，0 表示只靠回调通知触发拉取
    # 存档应用「接收事件服务器」配置，收到 msgaudit_notify 时立即拉取
    wecom_callback_token: str = ""
    wecom_callback_aes_key: str = ""
    # 「客户联系」Secret，可选：用于查询外部客户群名称
    wecom_contact_secret: str = ""

    @property
    def wecom_archive_enabled(self) -> bool:
        return bool(self.wecom_corp_id and self.wecom_archive_secret and self.wecom_archive_private_key_files)


settings = Settings()
