import { useState, type FormEvent } from "react";
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

/** 登录页；还没有任何账号时，先设管理员。 */
export default function Login({ setup, onDone }: { setup: boolean; onDone: (me: Staff) => void }) {
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
            autoFocus={!setup}
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
        <button type="submit" className="primary" disabled={busy || !username.trim() || !password || (setup && (!name.trim() || !again))}>
          {busy ? "请稍候…" : setup ? "设好并登录" : "登录"}
        </button>
        {!setup && <p className="auth-hint center">没有账号或忘了密码，请找管理员。</p>}
      </form>
    </div>
  );
}
