import { useEffect, useRef, useState, type FormEvent } from "react";
import { changePassword, logout, ROLE_LABEL, setMyStatus, type Staff, type StaffStatus } from "./api";

const STATUS: { key: StaffStatus; label: string; hint: string }[] = [
  { key: "online", label: "在线", hint: "可以接新会话" },
  { key: "away", label: "离开", hint: "暂不接新会话" },
];

interface Props {
  me: Staff;
  onChange: (me: Staff) => void;
  onLogout: () => void;
  notify: (text: string) => void;
}

/** 右上角：我是谁、在线状态、改密码、退出。 */
export default function UserMenu({ me, onChange, onLogout, notify }: Props) {
  const [open, setOpen] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = async (status: StaffStatus) => {
    setOpen(false);
    try {
      onChange(await setMyStatus(status));
    } catch (e) {
      notify(`切换失败：${(e as Error).message}`);
    }
  };

  const quit = async () => {
    setOpen(false);
    try {
      await logout();
    } finally {
      onLogout();
    }
  };

  return (
    <div className="user-menu" ref={box}>
      <button className="user-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className="ava">
          {me.name.slice(0, 1)}
          <i className={`st-dot ${me.status}`} />
        </span>
        <span className="user-name">
          <b>{me.name}</b>
          <small>
            {ROLE_LABEL[me.role]}
            {me.team ? ` · ${me.team.name}` : ""} · {me.status === "online" ? "在线" : "离开"}
          </small>
        </span>
        <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
          <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-label">我的状态</div>
          {STATUS.map((s) => (
            <button key={s.key} role="menuitemradio" aria-checked={me.status === s.key} className={me.status === s.key ? "on" : ""} onClick={() => pick(s.key)}>
              <i className={`st-dot ${s.key}`} />
              {s.label}
              <small>{s.hint}</small>
            </button>
          ))}
          <hr />
          <button role="menuitem" onClick={() => { setOpen(false); setPwOpen(true); }}>修改密码</button>
          <button role="menuitem" onClick={quit}>退出登录</button>
        </div>
      )}
      {pwOpen && <PasswordDialog onClose={() => setPwOpen(false)} notify={notify} />}
    </div>
  );
}

function PasswordDialog({ onClose, notify }: { onClose: () => void; notify: (text: string) => void }) {
  const [old, setOld] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (next !== again) return setError("两次输入的新密码不一样");
    setBusy(true);
    try {
      await changePassword(old, next);
      notify("密码已修改");
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-mask" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog" onSubmit={submit}>
        <h3>修改密码</h3>
        <label>
          原密码
          <input type="password" autoFocus autoComplete="current-password" value={old} onChange={(e) => setOld(e.target.value)} />
        </label>
        <label>
          新密码
          <input type="password" autoComplete="new-password" placeholder="至少 8 位" value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label>
          再输一次新密码
          <input type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} />
        </label>
        {error && <p className="auth-error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>取消</button>
          <button type="submit" className="primary" disabled={busy || !old || !next || !again}>保存</button>
        </div>
      </form>
    </div>
  );
}
