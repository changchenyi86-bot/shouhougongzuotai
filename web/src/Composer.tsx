import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  adopt,
  attachmentUrl,
  dismiss,
  fetchMembers,
  PRODUCT_CODES,
  PRODUCT_LABEL,
  productLabel,
  regenerate,
  sendToGroup,
  setSuggestionProduct,
  uploadAttachment,
  type Attachment,
  type InboxItem,
  type Member,
  type ProductCode,
  type Suggestion,
} from "./api";
import AiChat from "./AiChat";
import CopyButton, { copyText } from "./CopyButton";
import Knowledge from "./Knowledge";
import { useImageViewer } from "./ImageViewer";
import { FileIcon, formatSize, isImage } from "./MessageBody";
import { senderName } from "./Sender";
import {
  MentionBackdrop,
  MentionMenu,
  activeQuery,
  filterMembers,
  findMentions,
  memberOf,
  toRefs,
  type Picked,
} from "./Mentions";
import { MOD, SEND_KEYS, loadSendKey, saveSendKey, type SendKey } from "./sendKey";

const CONFIDENCE: Record<string, string> = { high: "把握高", medium: "把握一般", low: "把握低" };
const MAX_FILES = 9;

type Pending = { key: number; name: string; att?: Attachment; error?: string };
type ChatMessage = InboxItem["message"];

/** 「回复 谁：『说了什么』」：一条建议回同一轮好几条消息时，列出是谁、几条 */
function roundLabel(msgs: ChatMessage[]): { who: string; text: string } {
  const names = [...new Set(msgs.map((m) => senderName(m)))];
  const who = names.length > 2 ? `${names.slice(0, 2).join("、")} 等` : names.join("、");
  if (msgs.length <= 1) return { who, text: msgs[0]?.content ?? "" };
  return { who: `${who}（${msgs.length} 条）`, text: msgs.map((m) => m.content).join(" / ") };
}

const clip = (text: string, n: number) => (text.length > n ? `${text.slice(0, n)}…` : text);
/** 输入框里的文字从哪来：AI 建议、和 AI 的对话、自己打的 */
type Basis = "suggestion" | "chat" | "typed" | null;

interface Props {
  channel: InboxItem["message"]["channel"];
  groupId: string;
  groupName: string | null;
  canAutoSend: boolean;
  /** 当前要处理的客户消息（带 AI 建议）。 */
  focus: InboxItem | null;
  /** 这条建议回的同一轮客户消息（连发、几个人各说一句），按时间顺序；没有就是 focus 那一条 */
  round?: ChatMessage[];
  focusIndex: number;
  openTotal: number;
  onFocusStep: (delta: number) => void;
  onChanged: () => void;
  notify: (text: string) => void;
  /** 客户档案里只给内部看的提醒（如这个群对应产品线的维保已过期），显示在 AI 建议下方，不会进回复 */
  alerts?: string[];
}

/** 会话底部：AI 建议 + 输入框（文字、图片、文件），确认后由发送电脑发到群里。 */
export default function Composer(props: Props) {
  const { channel, groupId, groupName, canAutoSend, focus, onChanged, notify } = props;
  const [text, setText] = useState("");
  const [files, setFiles] = useState<Pending[]>([]);
  const [linked, setLinked] = useState<InboxItem | null>(null);
  const [basis, setBasis] = useState<Basis>(null);
  const [unlinked, setUnlinked] = useState(false); // 点了 ×，自己打字时不再自动关联
  const [chatOpen, setChatOpen] = useState(false);
  const [filledChat, setFilledChat] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [sendKey, setSendKey] = useState<SendKey>(loadSendKey);
  const [keyMenu, setKeyMenu] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const textInput = useRef<HTMLTextAreaElement>(null);
  const keyMenuBox = useRef<HTMLDivElement>(null);
  const seq = useRef(0);
  // @ 群成员
  const canMention = channel === "wecom"; // 目前只有企业微信能由发送电脑真正 @ 人
  const [members, setMembers] = useState<Member[] | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Picked>({});
  const [caret, setCaret] = useState(0);
  const [dismissedAt, setDismissedAt] = useState<number | null>(null); // 按 Esc 关掉的那个 @ 不再弹
  const [menuIndex, setMenuIndex] = useState(0);
  const [menuLeft, setMenuLeft] = useState(0);
  const backdrop = useRef<HTMLDivElement>(null);
  const caretMark = useRef<HTMLSpanElement>(null);
  const inputBox = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!canMention) return;
    let alive = true;
    fetchMembers(channel, groupId)
      .then((list) => alive && setMembers(list))
      .catch((e: Error) => alive && setMembersError(e.message));
    return () => {
      alive = false;
    };
  }, [canMention, channel, groupId]);

  const spans = canMention ? findMentions(text, members ?? [], picked) : [];
  const typing = canMention && caret >= 0 ? activeQuery(text, caret) : null;
  const menuOpen = !!typing && typing.start !== dismissedAt;
  const menuList = menuOpen && members ? filterMembers(members, typing!.query) : [];

  // 成员列表对齐到 @ 所在的位置
  useLayoutEffect(() => {
    if (!menuOpen) return;
    const mark = caretMark.current;
    const width = inputBox.current?.clientWidth ?? 0;
    if (mark) setMenuLeft(Math.max(0, Math.min(mark.offsetLeft - (backdrop.current?.scrollLeft ?? 0), width - 320)));
  }, [menuOpen, typing?.start, text]);

  useEffect(() => setMenuIndex(0), [typing?.query, typing?.start]);

  const syncCaret = () => {
    const el = textInput.current;
    if (el) setCaret(el.selectionStart === el.selectionEnd ? el.selectionStart : -1);
  };

  /** 把光标前正在输入的「@筛选文字」换成「@名字 」 */
  const pickMember = (m: Member) => {
    const el = textInput.current;
    if (!typing || !el) return;
    const end = caret;
    const insert = `@${m.name} `;
    const next = text.slice(0, typing.start) + insert + text.slice(end);
    const pos = typing.start + insert.length;
    setPicked((prev) => ({ ...prev, [m.name]: m }));
    onType(next);
    setCaret(pos);
    // 马上把光标放到「@名字 」后面：选完立刻接着打字时，字要打在空格后面
    el.value = next;
    el.setSelectionRange(pos, pos);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(pos, pos);
      setCaret(pos); // 程序改的光标不一定触发 select 事件
    });
  };

  /** 在光标处打一个 @，弹出成员列表 */
  const startMention = () => {
    const el = textInput.current;
    if (!el) return;
    const at = el.selectionStart ?? text.length;
    const before = text.slice(0, at);
    const insert = before && !/\s$/.test(before) ? " @" : "@";
    const next = before + insert + text.slice(el.selectionEnd ?? at);
    const pos = at + insert.length;
    setDismissedAt(null);
    onType(next);
    setCaret(pos);
    el.value = next;
    el.setSelectionRange(pos, pos);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(pos, pos);
      setCaret(pos); // 程序改的光标不一定触发 select 事件
    });
  };

  /** 这条建议回的是哪几条消息：正在处理的那条用整轮，其他的就是它自己 */
  const roundOf = (item: InboxItem | null): ChatMessage[] => {
    if (!item) return [];
    const same = !!item.suggestion && item.suggestion.id === focus?.suggestion?.id;
    return same && props.round?.length ? props.round : [item.message];
  };
  /** 这一轮里提问的客户（去重），回复开头要 @ 的人 */
  const askersOf = (item: InboxItem | null): Member[] => {
    if (!canMention) return [];
    const out: Member[] = [];
    for (const m of roundOf(item)) {
      const who = m.from_customer ? memberOf(m, members ?? []) : null;
      if (who && !out.some((x) => x.name === who.name)) out.push(who);
    }
    return out;
  };

  /** 在回复开头 @ 提问的客户（已经 @ 了就不重复） */
  const withAt = (content: string, item: InboxItem | null): string => {
    const missing = askersOf(item).filter((who) => !content.includes(`@${who.name}`));
    if (!missing.length) return content;
    setPicked((prev) => ({ ...prev, ...Object.fromEntries(missing.map((who) => [who.name, who])) }));
    return `${missing.map((who) => `@${who.name} `).join("")}${content}`;
  };

  const uploading = files.some((f) => !f.att && !f.error);
  const ready = files.filter((f) => f.att).map((f) => f.att!);
  const readyImages = ready.filter(isImage);
  const viewImage = useImageViewer();
  const hasContent = !!text.trim() || ready.length > 0;
  const canSend = canAutoSend && hasContent && !uploading && !!groupName;

  // 发送快捷键菜单：点别处或按 Esc 收起
  useEffect(() => {
    if (!keyMenu) return;
    const onDown = (e: MouseEvent) => {
      if (!keyMenuBox.current?.contains(e.target as Node)) setKeyMenu(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setKeyMenu(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [keyMenu]);

  const pickSendKey = (key: SendKey) => {
    setSendKey(key);
    saveSendKey(key);
    setKeyMenu(false);
    textInput.current?.focus();
  };

  const addFiles = (list: FileList | File[]) => {
    const room = MAX_FILES - files.length;
    const picked = Array.from(list).slice(0, Math.max(0, room));
    if (list.length > picked.length) notify(`一次最多发 ${MAX_FILES} 个图片或文件`);
    for (const file of picked) {
      const key = ++seq.current;
      const name = file.name || "粘贴的图片.png";
      setFiles((prev) => [...prev, { key, name }]);
      uploadAttachment(file.name ? file : new File([file], name, { type: file.type }))
        .then((att) => setFiles((prev) => prev.map((f) => (f.key === key ? { ...f, att } : f))))
        .catch((e: Error) => setFiles((prev) => prev.map((f) => (f.key === key ? { ...f, error: e.message } : f))));
    }
    setConfirming(false);
  };

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (e) {
      notify(`操作失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setText("");
    setPicked({});
    setDismissedAt(null);
    setFiles([]);
    setLinked(null);
    setBasis(null);
    setUnlinked(false);
    setFilledChat(null);
    setConfirming(false);
  };

  // 换了要回复的客户消息：自己打的字跟着关联到新的那条，对话小窗收起
  const focusId = focus?.suggestion?.id ?? null;
  useEffect(() => {
    setChatOpen(false);
    if (basis === "typed" && !unlinked) setLinked(focus);
  }, [focusId]); // 只在换消息时执行

  const send = () =>
    run(async () => {
      await sendToGroup({
        channel,
        group_id: groupId,
        content: text.trim(),
        attachment_ids: ready.map((a) => a.id),
        suggestion_id: linked?.suggestion?.id ?? null,
        via_chat: basis === "chat",
        mentions: toRefs(spans),
      });
      reset();
      notify("已确认，正在发送到群里");
    });

  // 发送电脑不在线：只能复制文字，到企业微信里手动发
  const copyOnly = () =>
    run(async () => {
      const content = text.trim();
      const at = toRefs(findMentions(content, members ?? [], picked));
      if (linked?.suggestion) await adopt(linked.suggestion.id, content, false, basis === "chat", at);
      const ok = await copyText(content);
      reset();
      notify(
        !ok
          ? "复制失败，请手动选择文字复制"
          : at.length
            ? "已复制。粘贴的「@名字」不会提醒对方，要提醒请在企业微信里重新打 @ 选人"
            : "已复制，请到企业微信群里粘贴发送",
      );
    });

  // 按发送键：第一次弹出确认条，确认条在时再按一次才真正发出
  const onKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    const composing = e.nativeEvent.isComposing || e.keyCode === 229;
    // 成员列表开着：上下选人，Enter / Tab 选中，Esc 关掉
    if (menuOpen && !composing) {
      if (e.key === "Escape") {
        e.preventDefault();
        setDismissedAt(typing!.start);
        return;
      }
      if (menuList.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        const d = e.key === "ArrowDown" ? 1 : -1;
        setMenuIndex((i) => (i + d + menuList.length) % menuList.length);
        return;
      }
      if (menuList.length && (e.key === "Enter" || e.key === "Tab") && !e.shiftKey) {
        e.preventDefault();
        pickMember(menuList[Math.min(menuIndex, menuList.length - 1)]);
        return;
      }
    }
    // 退格紧跟在「@名字」后面：整个删掉，和企业微信里一样
    if (e.key === "Backspace" && !composing && !e.altKey && !e.metaKey && !e.ctrlKey) {
      const el = e.currentTarget;
      const pos = el.selectionStart;
      if (pos === el.selectionEnd) {
        const hit = spans.find((s) => s.end === pos || (s.end + 1 === pos && text[s.end] === " "));
        if (hit) {
          e.preventDefault();
          onType(text.slice(0, hit.start) + text.slice(pos));
          setCaret(hit.start);
          requestAnimationFrame(() => el.setSelectionRange(hit.start, hit.start));
          return;
        }
      }
    }
    if (e.key === "Escape" && confirming) {
      e.preventDefault();
      setConfirming(false);
      return;
    }
    if (e.key !== "Enter" || e.altKey) return;
    // 输入法选字时按的回车只是把字母上屏，不算发送
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    const mod = e.ctrlKey || e.metaKey;
    const isSendKey = !e.shiftKey && (sendKey === "enter" ? !mod : mod);
    if (!isSendKey) {
      // Enter 发送时，Command/Ctrl+Enter 和 Shift+Enter 一样换行
      if (sendKey === "enter" && mod) {
        e.preventDefault();
        const el = e.currentTarget;
        el.setRangeText("\n", el.selectionStart, el.selectionEnd, "end");
        setText(el.value);
        setConfirming(false);
      }
      return;
    }
    // 发送电脑不在线时只能复制文字，发送键照常换行
    if (!canAutoSend) return;
    e.preventDefault();
    if (e.repeat || busy) return;
    if (!hasContent) return;
    if (uploading) return notify("图片或文件还在上传，传完再发");
    if (!groupName) return notify("还不知道这个群的名字，无法自动发送");
    if (confirming) send();
    else setConfirming(true);
  };

  const applySuggestion = (item: InboxItem) => {
    setText(withAt(item.suggestion?.content ?? "", item));
    setLinked(item);
    setBasis("suggestion");
    setFilledChat(null);
    setConfirming(false);
  };

  const fillFromChat = (content: string) => {
    if (!focus) return;
    setText(withAt(content, focus));
    setLinked(focus);
    setBasis("chat");
    setConfirming(false);
    textInput.current?.focus();
  };

  const onType = (value: string) => {
    setText(value);
    setConfirming(false);
    if (!value.trim()) {
      if (basis === "typed") {
        setBasis(null);
        if (!unlinked) setLinked(null);
      }
      return;
    }
    // 自己打的回复也关联到正在处理的客户消息，发出后记为已回复（可以点 × 取消关联）
    if (!basis) {
      setBasis("typed");
      if (!linked && !unlinked && focus?.suggestion) setLinked(focus);
    }
  };

  const s = focus?.suggestion;
  const inUse = !!s && linked?.suggestion?.id === s.id && basis === "suggestion"; // 建议已填进输入框，面板收起
  const maybeNoReply = !!s && s.status === "ready" && s.needs_reply === false && !s.reopened_by;
  const atNames = [...new Set(spans.map((sp) => sp.member.name))];
  const atDup = [...new Set(spans.filter((sp) => sp.member.duplicate).map((sp) => sp.member.name))];
  const askersToAt = askersOf(linked).filter((who) => !spans.some((sp) => sp.member.name === who.name));
  const focusAskers = s?.content ? askersOf(focus).filter((who) => !s.content!.includes(`@${who.name}`)) : [];
  const focusLabel = roundLabel(roundOf(focus));
  const linkedLabel = roundLabel(roundOf(linked));
  // AI 结合上下文补全的问题：和客户原话一样就不用再显示
  const question = s?.question?.trim() && s.question.trim() !== focusLabel.text.trim() ? s.question.trim() : null;
  const what = [
    text.trim() && "文字",
    readyImages.length && `${readyImages.length} 张图片`,
    ready.length - readyImages.length && `${ready.length - readyImages.length} 个文件`,
  ].filter(Boolean).join("、");

  return (
    <div
      className={`composer ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => {
        if (!canAutoSend) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        setDragging(false);
        if (!canAutoSend || !e.dataTransfer.files.length) return;
        e.preventDefault();
        addFiles(e.dataTransfer.files);
      }}
    >
      {focus && s && (
        <div className={`ai-panel ${s.status}`}>
          <div className="ai-head">
            <b>AI 建议</b>
            <span className="ai-for" title={roundOf(focus).map((m) => `${senderName(m)}：${m.content}`).join("\n")}>
              回复 {focusLabel.who}：「{clip(focusLabel.text, 40)}」
            </span>
            {props.openTotal > 1 && (
              <span className="ai-nav">
                <button className="link" disabled={props.focusIndex <= 0} onClick={() => props.onFocusStep(-1)}>‹</button>
                待回复 {props.focusIndex + 1}/{props.openTotal}
                <button className="link" disabled={props.focusIndex >= props.openTotal - 1} onClick={() => props.onFocusStep(1)}>›</button>
              </span>
            )}
          </div>
          {s.status === "generating" && <p className="ai-text muted">正在生成回复建议…</p>}
          {s.status === "failed" && <p className="ai-text err">生成失败：{s.error}</p>}
          {inUse && <p className="ai-text muted">已填入下方输入框，可以修改后发送。</p>}
          {!inUse && s.status === "ready" && (
            <>
              <div className="sug-head">
                {maybeNoReply ? (
                  <span className="badge">AI 觉得可能不用回复</span>
                ) : s.can_answer ? (
                  <span className="badge ok">可直接回复</span>
                ) : (
                  <span className="badge warn">AI 没把握</span>
                )}
                {s.confidence && <span className="conf">{CONFIDENCE[s.confidence]}</span>}
                {s.reason && <span className="reason">{s.reason}</span>}
              </div>
              {question && (
                <p className="ai-question" title="AI 结合前面的聊天，把这一轮客户要解决的问题补全成一句话，再拿这句去查知识">
                  <b>AI 理解的问题</b>
                  <span>{question}</span>
                </p>
              )}
              {s.products && s.products.length > 0 && (
                <ProductLine s={s} busy={busy} onChange={(code) => run(() => setSuggestionProduct(s.id, [code]))} />
              )}
              {s.note && <p className="note">备注：{s.note}</p>}
              {s.content && (
                <p className="ai-text">
                  {focusAskers.map((who) => (
                    <span key={who.name}>
                      <mark className="mention" title="用这条建议时会在开头 @ 这位客户，发出后对方会收到 @ 提醒">
                        @{who.name}
                      </mark>{" "}
                    </span>
                  ))}
                  {s.content}
                </p>
              )}
              <Knowledge s={s} />
              {props.alerts && props.alerts.length > 0 && (
                <p className="inner-warn">
                  <b>仅内部</b>
                  <span>
                    客户档案：{props.alerts.join("；")}。回复前确认是否继续支持。这句不会写进回复。
                  </span>
                </p>
              )}
            </>
          )}
          <div className="actions">
            {s.content && s.status !== "generating" && !inUse && (
              <button className="primary" disabled={busy} onClick={() => applySuggestion(focus)}>用这条建议</button>
            )}
            <button
              className={`ai-chat-toggle ${chatOpen ? "on" : ""}`}
              aria-expanded={chatOpen}
              title="说说你的方案或要点，AI 帮你写成发给客户的回复"
              onClick={() => setChatOpen((v) => !v)}
            >
              和 AI 对话
            </button>
            <button disabled={busy} onClick={() => run(() => dismiss(s.id))}>无需回复</button>
            {s.status !== "generating" && (
              <button disabled={busy} onClick={() => run(() => regenerate(s.id))}>重新生成</button>
            )}
            {s.content && !inUse && s.status === "ready" && (
              <CopyButton text={s.content} label="复制" title="复制 AI 建议的文字" />
            )}
          </div>
          {chatOpen && (
            <AiChat
              suggestion={s}
              onFill={fillFromChat}
              filledId={basis === "chat" ? filledChat : null}
              onFilled={setFilledChat}
              notify={notify}
            />
          )}
        </div>
      )}

      {linked && (
        <div className="reply-to">
          回复 {linkedLabel.who}：「{clip(linkedLabel.text, 30)}」
          <span className="muted">（发出后{roundOf(linked).length > 1 ? "这几条" : "这条"}消息记为已处理）</span>
          {askersToAt.map((asker) => (
            <button
              key={asker.name}
              className="at-asker"
              title={`在回复开头 @ ${asker.name}，对方会收到 @ 提醒`}
              onClick={() => {
                setPicked((prev) => ({ ...prev, [asker.name]: asker }));
                onType(`@${asker.name} ${text.replace(/^\s+/, "")}`);
                textInput.current?.focus();
              }}
            >
              @ {asker.name}
            </button>
          ))}
          <button
            className="link"
            onClick={() => {
              setLinked(null);
              setUnlinked(true);
            }}
            title="不关联这条消息"
          >
            ×
          </button>
        </div>
      )}

      {files.length > 0 && (
        <div className="chips">
          {files.map((f) => (
            <span key={f.key} className={`chip ${f.error ? "err" : ""}`} title={f.error}>
              {f.att && isImage(f.att) ? (
                <button type="button" className="chip-img" onClick={() => viewImage(f.att!, readyImages)} title="点击查看大图">
                  <img src={attachmentUrl(f.att.id)} alt="" />
                  {f.name}
                </button>
              ) : (
                <>
                  <FileIcon name={f.name} size={14} />
                  {f.name}
                </>
              )}
              {f.att && <small>{formatSize(f.att.size)}</small>}
              {!f.att && !f.error && <small>上传中…</small>}
              {f.error && <small>上传失败：{f.error}</small>}
              <button className="link" onClick={() => setFiles((prev) => prev.filter((x) => x.key !== f.key))}>×</button>
            </span>
          ))}
        </div>
      )}

      <div className={`composer-input ${spans.length ? "has-mention" : ""}`} ref={inputBox}>
        {canMention && (
          <MentionBackdrop ref={backdrop} text={text} spans={spans} caretAt={menuOpen ? typing!.start : null} caretRef={caretMark} />
        )}
        {menuOpen && (
          <MentionMenu
            members={members}
            error={membersError}
            query={typing!.query}
            index={Math.min(menuIndex, Math.max(0, menuList.length - 1))}
            left={menuLeft}
            onPick={pickMember}
            onHover={setMenuIndex}
          />
        )}
      <textarea
        ref={textInput}
        value={text}
        placeholder={
          canAutoSend
            ? `输入回复，可以粘贴或拖入图片、文件。${sendKey === "enter" ? "Enter 发送，Shift+Enter 换行" : `${MOD}+Enter 发送`}`
            : "输入回复。发送电脑不在线，只能复制文字后到企业微信里手动发"
        }
        rows={Math.min(8, Math.max(3, text.split("\n").length + 1))}
        onChange={(e) => {
          onType(e.target.value);
          setCaret(e.target.selectionStart === e.target.selectionEnd ? e.target.selectionStart : -1);
        }}
        onSelect={syncCaret}
        onBlur={() => setCaret(-1)}
        onFocus={syncCaret}
        onScroll={(e) => {
          if (backdrop.current) backdrop.current.scrollTop = e.currentTarget.scrollTop;
        }}
        onPaste={(e) => {
          if (canAutoSend && e.clipboardData.files.length) {
            e.preventDefault();
            addFiles(e.clipboardData.files);
          }
        }}
        onKeyDown={onKeyDown}
      />
      </div>

      {confirming && (
        <div className="confirm-send">
          <span>
            将以客服账号把{what}发到企业微信群「<b>{groupName}</b>」
            {atNames.length > 0 && <>，并 @ {atNames.join("、")}</>}，确认发送？
            {atDup.length > 0 && (
              <small className="confirm-warn">
                群里不止一个人叫「{atDup.join("」「")}」，发送电脑分不清时会按普通文字发出，对方收不到 @ 提醒。
              </small>
            )}
          </span>
          <button className="primary" disabled={busy} onClick={send}>确认发送</button>
          <button disabled={busy} onClick={() => setConfirming(false)}>取消</button>
        </div>
      )}

      <div className="composer-bar">
        <input
          ref={fileInput}
          type="file"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files) addFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          className="tool"
          disabled={!canAutoSend || files.length >= MAX_FILES}
          title={canAutoSend ? "添加图片或文件" : "发送电脑不在线，暂时不能发图片和文件"}
          onClick={() => fileInput.current?.click()}
        >
          📎 图片 / 文件
        </button>
        {canMention && (
          <button className="tool" title="@ 群里的人，对方会收到 @ 提醒（输入框里打 @ 也可以）" onClick={startMention}>
            @ 群成员
          </button>
        )}
        <span className="spacer" />
        {(text || files.length > 0) && (
          <button className="plain" disabled={busy} onClick={reset}>清空</button>
        )}
        <button className="plain" disabled={busy || !text.trim()} onClick={copyOnly}>
          {canAutoSend ? "只复制文字，我自己发" : "复制文字"}
        </button>
        {canAutoSend && (
          <div className="send-split" ref={keyMenuBox}>
            <button
              className="primary"
              disabled={busy || !canSend || confirming}
              title={groupName ? undefined : "还不知道这个群的名字，无法自动发送"}
              onClick={() => setConfirming(true)}
            >
              {uploading ? "上传中…" : "发送"}
            </button>
            <button
              className={`primary caret ${busy || !canSend || confirming ? "dim" : ""}`}
              aria-label="选择发送快捷键"
              aria-haspopup="menu"
              aria-expanded={keyMenu}
              title="选择发送快捷键"
              onClick={() => setKeyMenu((v) => !v)}
            >
              <svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
                <path d="M3 4.5 6 7.5 9 4.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            {keyMenu && (
              <div className="send-menu" role="menu">
                {SEND_KEYS.map((k) => (
                  <button
                    key={k.key}
                    role="menuitemradio"
                    aria-checked={sendKey === k.key}
                    className={sendKey === k.key ? "on" : ""}
                    onClick={() => pickSendKey(k.key)}
                  >
                    {k.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}


const PRODUCT_SOURCE: Record<string, string> = {
  manual: "管理员给这个群指定的",
  group: "按群名",
  ai: "AI 按这一轮内容判断",
  all: "AI 判断不出，几个产品一起查",
};

/** 建议卡：这条建议查的是哪个产品的知识、怎么定的；查错了可以改，改完按新产品重新生成。 */
function ProductLine({ s, busy, onChange }: { s: Suggestion; busy: boolean; onChange: (code: ProductCode) => void }) {
  const codes = s.products ?? [];
  const how =
    s.product_source === "override"
      ? `${s.product_corrected_by ?? "技术支持"}改的`
      : PRODUCT_SOURCE[s.product_source ?? ""] ?? "";
  const reason = s.product_source === "ai" && s.product_reason ? `：${s.product_reason}` : "";
  const editable = s.status === "ready" || s.status === "failed";
  return (
    <p className="ai-product" title="去售后数字人哪个产品的知识库查资料。查错了就改，改完按新产品重新生成这条建议">
      <b>查的知识</b>
      <span className="ai-product-name">{productLabel(codes)}</span>
      <span className="ai-product-how">
        {how}
        {reason}
      </span>
      {editable && (
        <select
          aria-label="改成别的产品"
          value=""
          disabled={busy}
          onChange={(e) => e.target.value && onChange(e.target.value as ProductCode)}
        >
          <option value="">改成…</option>
          {PRODUCT_CODES.filter((c) => !(codes.length === 1 && codes[0] === c)).map((c) => (
            <option key={c} value={c}>
              {PRODUCT_LABEL[c]}
            </option>
          ))}
        </select>
      )}
    </p>
  );
}
