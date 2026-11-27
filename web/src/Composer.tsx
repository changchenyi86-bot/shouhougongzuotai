import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import {
  adopt,
  attachmentUrl,
  dismiss,
  escalate,
  regenerate,
  sendToGroup,
  uploadAttachment,
  type Attachment,
  type InboxItem,
} from "./api";
import AiChat from "./AiChat";
import Knowledge from "./Knowledge";
import { useImageViewer } from "./ImageViewer";
import { FileIcon, formatSize, isImage } from "./MessageBody";
import { senderName } from "./Sender";
import { MOD, SEND_KEYS, loadSendKey, saveSendKey, type SendKey } from "./sendKey";

const CONFIDENCE: Record<string, string> = { high: "把握高", medium: "把握一般", low: "把握低" };
const MAX_FILES = 9;

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

type Pending = { key: number; name: string; att?: Attachment; error?: string };
/** 输入框里的文字从哪来：AI 建议、和 AI 的对话、自己打的 */
type Basis = "suggestion" | "chat" | "typed" | null;

interface Props {
  channel: InboxItem["message"]["channel"];
  groupId: string;
  groupName: string | null;
  canAutoSend: boolean;
  /** 当前要处理的客户消息（带 AI 建议）。 */
  focus: InboxItem | null;
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
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");
  const [dragging, setDragging] = useState(false);
  const [sendKey, setSendKey] = useState<SendKey>(loadSendKey);
  const [keyMenu, setKeyMenu] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const textInput = useRef<HTMLTextAreaElement>(null);
  const keyMenuBox = useRef<HTMLDivElement>(null);
  const seq = useRef(0);

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
    setFiles([]);
    setLinked(null);
    setBasis(null);
    setUnlinked(false);
    setFilledChat(null);
    setConfirming(false);
  };

  // 换了要回复的客户消息：自己打的字跟着关联到新的那条，对话小窗和转人工备注收起
  const focusId = focus?.suggestion?.id ?? null;
  useEffect(() => {
    setChatOpen(false);
    setNoteOpen(false);
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
      });
      reset();
      notify("已确认，正在发送到群里");
    });

  // 发送电脑不在线：只能复制文字，到企业微信里手动发
  const copyOnly = () =>
    run(async () => {
      const content = text.trim();
      if (linked?.suggestion) await adopt(linked.suggestion.id, content, false, basis === "chat");
      const ok = await copy(content);
      reset();
      notify(ok ? "已复制，请到企业微信群里粘贴发送" : "复制失败，请手动选择文字复制");
    });

  // 按发送键：第一次弹出确认条，确认条在时再按一次才真正发出
  const onKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
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
    setText(item.suggestion?.content ?? "");
    setLinked(item);
    setBasis("suggestion");
    setFilledChat(null);
    setConfirming(false);
  };

  const fillFromChat = (content: string) => {
    if (!focus) return;
    setText(content);
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
            <span className="ai-for">
              回复 {senderName(focus.message)}：「{focus.message.content.slice(0, 40)}
              {focus.message.content.length > 40 ? "…" : ""}」
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
          {!inUse && (s.status === "ready" || s.status === "escalated") && (
            <>
              <div className="sug-head">
                {s.status === "escalated" ? (
                  <span className="badge warn">已转人工{s.handled_by ? ` · ${s.handled_by}` : ""}</span>
                ) : maybeNoReply ? (
                  <span className="badge">AI 觉得可能不用回复</span>
                ) : s.can_answer ? (
                  <span className="badge ok">可直接回复</span>
                ) : (
                  <span className="badge warn">AI 建议转人工</span>
                )}
                {s.confidence && <span className="conf">{CONFIDENCE[s.confidence]}</span>}
                {s.reason && <span className="reason">{s.reason}</span>}
              </div>
              {s.note && <p className="note">转人工备注：{s.note}</p>}
              {s.content && <p className="ai-text">{s.content}</p>}
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
            {s.status !== "escalated" && s.status !== "generating" && (
              <button disabled={busy} onClick={() => setNoteOpen((v) => !v)}>转人工</button>
            )}
            <button disabled={busy} onClick={() => run(() => dismiss(s.id))}>无需回复</button>
            {s.status !== "escalated" && s.status !== "generating" && (
              <button disabled={busy} onClick={() => run(() => regenerate(s.id))}>重新生成</button>
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
          {noteOpen && (
            <div className="escalate">
              <input placeholder="备注（可选）：需要谁处理、缺什么信息" value={note} onChange={(e) => setNote(e.target.value)} />
              <button
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await escalate(s.id, note);
                    setNoteOpen(false);
                    setNote("");
                  })
                }
              >
                确认转人工
              </button>
            </div>
          )}
        </div>
      )}

      {linked && (
        <div className="reply-to">
          回复 {senderName(linked.message)}：「{linked.message.content.slice(0, 30)}」
          <span className="muted">（发出后这条消息记为已处理）</span>
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

      <textarea
        ref={textInput}
        value={text}
        placeholder={
          canAutoSend
            ? `输入回复，可以粘贴或拖入图片、文件。${sendKey === "enter" ? "Enter 发送，Shift+Enter 换行" : `${MOD}+Enter 发送`}`
            : "输入回复。发送电脑不在线，只能复制文字后到企业微信里手动发"
        }
        rows={Math.min(8, Math.max(3, text.split("\n").length + 1))}
        onChange={(e) => onType(e.target.value)}
        onPaste={(e) => {
          if (canAutoSend && e.clipboardData.files.length) {
            e.preventDefault();
            addFiles(e.clipboardData.files);
          }
        }}
        onKeyDown={onKeyDown}
      />

      {confirming && (
        <div className="confirm-send">
          <span>
            将以客服账号把{what}发到企业微信群「<b>{groupName}</b>」，确认发送？
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
