import { useEffect, useState, type FormEvent } from "react";
import { login, setupAdmin, type Staff } from "./api";

export function Logo() {
  return (
    <span className="logo" aria-hidden>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
      </svg>
    </span>
  );
}

/** 公司登录没成功，回到登录页时地址里带的原因。 */
function ssoErrorText(q: URLSearchParams): string | null {
  const code = q.get("sso_error");
  if (!code) return null;
  const account = (q.get("account") ?? "").slice(0, 64);
  switch (code) {
    case "unbound":
      return `公司账号 ${account} 还没绑定工作台成员。请让管理员在「团队设置 → 成员」里绑定，绑好后再点一次「用网易账号登录」。`;
    case "disabled":
      return "这个账号已停用，请联系管理员。";
    case "denied":
      return "公司登录没有完成，可以再试一次。";
    case "expired":
      return "登录等太久或页面过期了，请再点一次「用网易账号登录」。";
    case "off":
      return "还没开通公司账号登录，请先用工作台账号密码登录。";
    default:
      return "公司登录校验没通过，请再试一次；一直不行请联系管理员。";
  }
}

/** 登录页；还没有任何账号时，先设管理员。配好公司单点登录后，可以用网易账号登录。 */
export default function Login({ setup, ssoUrl, onDone }: { setup: boolean; ssoUrl: string | null; onDone: (me: Staff) => void }) {
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [ssoError] = useState(() => ssoErrorText(new URLSearchParams(location.search)));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const withSso = !!ssoUrl && !setup;

  // 提示读到了，就把原因从地址栏去掉，刷新后不再显示
  useEffect(() => {
    if (new URLSearchParams(location.search).has("sso_error")) history.replaceState(null, "", location.pathname + location.hash);
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (setup && password !== again) return setError("两次输入的密码不一样");
    setBusy(true);
    try {
      onDone(setup ? await setupAdmin({ name, username, password }) : await login(username, password));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand">
          <Logo />
          <b>售后工作台</b>
        </div>
        <h2>{setup ? "第一次使用，先设管理员账号" : "登录"}</h2>
        {setup && <p className="auth-hint">管理员可以添加成员、指定群负责人。设好后会直接登录。</p>}
        {ssoError && <p className="auth-error">{ssoError}</p>}
        {withSso && (
          <>
            <a className="sso-btn" href={ssoUrl!}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4" />
                <path d="M10 17l5-5-5-5" />
                <path d="M15 12H3" />
              </svg>
              用网易账号登录
            </a>
            <div className="auth-or"><span>或用工作台账号密码</span></div>
          </>
        )}
        {setup && (
          <label>
            姓名
            <input value={name} maxLength={32} autoFocus placeholder="显示在处理记录里" onChange={(e) => setName(e.target.value)} />
          </label>
        )}
        <label>
          账号
          <input
            value={username}
            maxLength={64}
            autoFocus={!setup && !withSso}
            autoComplete="username"
            placeholder={setup ? "登录时用，比如姓名拼音" : undefined}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          密码
          <input
            type="password"
            value={password}
            maxLength={128}
            autoComplete={setup ? "new-password" : "current-password"}
            placeholder={setup ? "至少 8 位" : undefined}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {setup && (
          <label>
            再输一次密码
            <input type="password" value={again} maxLength={128} autoComplete="new-password" onChange={(e) => setAgain(e.target.value)} />
          </label>
        )}
        {error && <p className="auth-error">{error}</p>}
        <button type="submit" className={withSso ? "plain" : "primary"} disabled={busy || !username.trim() || !password || (setup && (!name.trim() || !again))}>
          {busy ? "请稍候…" : setup ? "设好并登录" : "登录"}
        </button>
        {!setup && (
          <p className="auth-hint center">
            {withSso ? "公司账号登录不了、或忘了工作台密码，请找管理员。" : "没有账号或忘了密码，请找管理员。"}
          </p>
        )}
      </form>
    </div>
  );
}
