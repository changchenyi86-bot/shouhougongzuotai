import { useCallback, useEffect, useRef, useState } from "react";
import { AUTH_REQUIRED, fetchAuthState, fetchCandidateCounts, fetchMe, fetchSendState, type SendState, type Staff } from "./api";
import Conversations from "./Conversations";
import EvalPage from "./EvalPage";
import KnowledgePage from "./KnowledgePage";
import Login, { Logo } from "./Login";
import StatsPage from "./StatsPage";
import TeamSettings from "./TeamSettings";
import UsagePage from "./UsagePage";
import { applyTheme, loadTheme, saveTheme, THEMES, type Theme } from "./theme";
import UserMenu from "./UserMenu";

// 线条图标（24×24 视图，描边绘制）
const ICONS: Record<string, string> = {
  会话: "M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5z",
  工单: "M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM9 8h6M9 12h6M9 16h3",
  知识库: "M4 5a2 2 0 0 1 2-2h5v18H6a2 2 0 0 1-2-2zM13 3h5a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-5z",
  采纳统计: "M4 19h16M7 16v-4M12 16V8M17 16v-6",
  评测: "M9 4h6M10 4v5.5L5.2 17.4A1.7 1.7 0 0 0 6.7 20h10.6a1.7 1.7 0 0 0 1.5-2.6L14 9.5V4M7.5 14h9",
  积分排名: "M5 20V11M12 20V5M19 20v-6",
  模型用量: "M4 16a8 8 0 1 1 16 0M12 16l3.5-4.5M3 20h18",
  收起: "M15 6l-6 6 6 6",
  展开: "M9 6l6 6-6 6",
  团队设置:
    "M16 19v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 17.5V19M10 10.5a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM20 19v-1.5a3.5 3.5 0 0 0-2.5-3.35M15.5 4.6a3 3 0 0 1 0 5.8",
};

type Page = "会话" | "知识库" | "采纳统计" | "评测" | "模型用量" | "团队设置";
const PAGES: Page[] = ["会话", "知识库", "采纳统计", "评测", "模型用量", "团队设置"];
const ADMIN_PAGES: Page[] = ["模型用量", "团队设置"];
const SOON = ["工单", "积分排名"];
const HASH: Record<Page, string> = { 会话: "", 知识库: "#knowledge", 采纳统计: "#stats", 评测: "#eval", 模型用量: "#usage", 团队设置: "#team" };

function pageFromHash(): Page {
  return PAGES.find((p) => HASH[p] && HASH[p] === location.hash) ?? "会话";
}

// 左侧导航默认收起成只有图标的窄条，展开/收起记在浏览器里
const NAV_KEY = "nav-expanded";

function loadNavExpanded(): boolean {
  try {
    return localStorage.getItem(NAV_KEY) === "1";
  } catch {
    return false;
  }
}

function saveNavExpanded(expanded: boolean) {
  try {
    localStorage.setItem(NAV_KEY, expanded ? "1" : "0");
  } catch {
    /* 浏览器禁用存储时忽略 */
  }
}

function Icon({ d }: { d: string }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

export default function App() {
  const [auth, setAuth] = useState<"loading" | "login" | "setup" | "in">("loading");
  const [me, setMe] = useState<Staff | null>(null);
  const [page, setPageState] = useState<Page>(pageFromHash);
  const [knowledgeBadge, setKnowledgeBadge] = useState(0);
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [navExpanded, setNavExpanded] = useState(loadNavExpanded);
  const [toast, setToast] = useState<string | null>(null);
  const [sendState, setSendState] = useState<SendState | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>();

  const notify = useCallback((text: string) => {
    setToast(text);
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  // 刷新页面时停在原来的页面
  const setPage = useCallback((p: Page) => {
    setPageState(p);
    history.replaceState(null, "", HASH[p] || location.pathname + location.search);
  }, []);

  const signedIn = (m: Staff) => {
    setMe(m);
    setAuth("in");
  };

  // 打开时看有没有登录；没有的话，还没设过管理员就先设管理员
  useEffect(() => {
    fetchMe()
      .then(signedIn)
      .catch(() =>
        fetchAuthState()
          .then((s) => setAuth(s.needs_setup ? "setup" : "login"))
          .catch(() => setAuth("login")),
      );
  }, []);

  // 任何接口说没登录（过期、被停用），回到登录页
  useEffect(() => {
    const out = () => {
      setMe(null);
      setPage("会话");
      setAuth("login");
    };
    window.addEventListener(AUTH_REQUIRED, out);
    return () => window.removeEventListener(AUTH_REQUIRED, out);
  }, []);

  // 角色、分组可能被管理员改了，定时取一次
  useEffect(() => {
    if (auth !== "in") return;
    const t = setInterval(() => fetchMe().then(setMe).catch(() => {}), 60_000);
    return () => clearInterval(t);
  }, [auth]);

  // 知识库角标：AI 建议沉淀、等我确认的 + 等我审核的
  const loadCounts = useCallback(() => {
    fetchCandidateCounts()
      .then((c) => setKnowledgeBadge(c.mine + c.pending))
      .catch(() => setKnowledgeBadge(0));
  }, []);

  useEffect(() => {
    if (auth !== "in") return;
    loadCounts();
    const timer = setInterval(loadCounts, 30_000);
    return () => clearInterval(timer);
  }, [auth, loadCounts]);

  const loadSendState = useCallback(() => {
    fetchSendState()
      .then(setSendState)
      .catch(() => setSendState(null));
  }, []);

  useEffect(() => {
    if (auth !== "in") return;
    loadSendState();
    const timer = setInterval(loadSendState, 10_000);
    return () => clearInterval(timer);
  }, [auth, loadSendState]);

  useEffect(() => {
    applyTheme(theme);
    saveTheme(theme);
  }, [theme]);

  const toggleNav = () => {
    const next = !navExpanded;
    setNavExpanded(next);
    saveNavExpanded(next);
  };

  if (auth === "loading") return <div className="auth-page" />;
  if (auth !== "in" || !me) return <Login setup={auth === "setup"} onDone={signedIn} />;

  const admin = me.role === "admin";
  const current: Page = ADMIN_PAGES.includes(page) && !admin ? "会话" : page;

  return (
    <div className="layout">
      <aside className={navExpanded ? "" : "collapsed"}>
        <h1 title={navExpanded ? undefined : "售后工作台"}>
          <Logo />
          <span className="brand">售后工作台</span>
        </h1>
        <nav>
          {(["会话", "知识库", "采纳统计", "评测"] as Page[]).map((p) => (
            <a key={p} href={HASH[p] || "#"} className={current === p ? "active" : ""} title={navExpanded ? undefined : p} onClick={(e) => {
              e.preventDefault();
              setPage(p);
            }}>
              <Icon d={ICONS[p]} />
              <span>{p}</span>
              {p === "知识库" && knowledgeBadge > 0 && (
                <small className="nav-badge" title="等你确认或审核的候选知识">{knowledgeBadge}</small>
              )}
            </a>
          ))}
          {SOON.map((label) => (
            <a key={label} className="disabled" title={navExpanded ? "即将上线" : `${label}（即将上线）`}>
              <Icon d={ICONS[label]} />
              <span>{label}</span>
              <small className="soon">即将上线</small>
            </a>
          ))}
        </nav>
        <nav className="nav-bottom">
          {admin &&
            ADMIN_PAGES.map((p) => (
              <a key={p} href={HASH[p]} className={current === p ? "active" : ""} title={navExpanded ? undefined : p} onClick={(e) => {
                e.preventDefault();
                setPage(p);
              }}>
                <Icon d={ICONS[p]} />
                <span>{p}</span>
              </a>
            ))}
          <a className="nav-toggle" role="button" title={navExpanded ? "收起导航" : "展开导航"} aria-expanded={navExpanded} tabIndex={0} onClick={toggleNav} onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              toggleNav();
            }
          }}>
            <Icon d={ICONS[navExpanded ? "收起" : "展开"]} />
            <span>收起导航</span>
          </a>
        </nav>
      </aside>
      <main>
        <header>
          <h2>{current}</h2>
          {current === "会话" && sendState?.enabled && <SendIndicator state={sendState} />}
          <div className="header-tools">
            <div className="theme-switch" role="radiogroup" aria-label="界面风格" title="切换界面风格">
              {THEMES.map((t) => (
                <button key={t.key} role="radio" aria-checked={theme === t.key} className={theme === t.key ? "on" : ""} onClick={() => setTheme(t.key)}>
                  <i className={t.key} />
                  {t.label}
                </button>
              ))}
            </div>
            <UserMenu
              me={me}
              onChange={setMe}
              onLogout={() => {
                setMe(null);
                setPage("会话");
                setAuth("login");
              }}
              notify={notify}
            />
          </div>
        </header>
        {/* 会话页切走时保留（选中的群、正在写的回复都还在） */}
        <div className={`page ${current === "会话" ? "" : "hidden"}`}>
          <Conversations
            me={me}
            canAutoSend={(c) => c === "wecom" && !!sendState?.ready}
            notify={notify}
            onOpenKnowledge={() => setPage("知识库")}
            onKnowledgeChanged={loadCounts}
          />
        </div>
        {current === "知识库" && <KnowledgePage me={me.name} notify={notify} onCountsChanged={loadCounts} />}
        {current === "采纳统计" && <StatsPage />}
        {current === "评测" && <EvalPage me={me} notify={notify} />}
        {current === "模型用量" && <UsagePage notify={notify} />}
        {current === "团队设置" && <TeamSettings me={me} notify={notify} />}
      </main>
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function SendIndicator({ state }: { state: SendState }) {
  const agent = state.agents[0];
  let text = "发送电脑离线：只能复制文字后手动发送";
  if (state.ready) text = `发送电脑在线（${state.agents.filter((a) => a.wecom_ready).map((a) => a.agent_id).join("、")}）`;
  else if (agent) text = `发送电脑：${agent.detail || "企业微信未就绪"}`;
  return (
    <span className={`send-indicator ${state.ready ? "on" : "off"}`} title="电脑端发送程序操作企业微信客户端，把确认后的消息发到群里">
      <i />
      {text}
    </span>
  );
}
