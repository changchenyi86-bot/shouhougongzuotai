import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { fetchChat, sendChat, type ChatTurn, type Suggestion } from "./api";
import CopyButton from "./CopyButton";

interface Props {
  suggestion: Suggestion;
  /** 把 AI 写的回复填进输入框 */
  onFill: (text: string) => void;
  /** 当前填在输入框里的那条（显示「已填入」） */
  filledId: string | null;
  onFilled: (id: string) => void;
  notify: (text: string) => void;
}

/** AI 建议下方的小窗：技术支持说自己的方案或要点，AI 写成可以发给客户的回复，可以多轮修改。只给技术支持看。 */
export default function AiChat({ suggestion, onFill, filledId, onFilled, notify }: Props) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState<string | null>(null); // 刚发给 AI、还在等回复的话
  const busy = sending !== null;
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    let alive = true;
    setTurns([]);
    fetchChat(suggestion.id)
      .then((t) => alive && setTurns(t))
      .catch(() => {
        /* 读不到历史对话时从头开始 */
      });
    inputRef.current?.focus();
    return () => {
      alive = false;
    };
  }, [suggestion.id]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, busy]);

  const submit = async () => {
    const content = input.trim();
    if (!content || busy) return;
    setSending(content);
    setInput("");
    try {
      setTurns(await sendChat(suggestion.id, content));
    } catch (e) {
      setInput(content); // 没发成，话放回输入框
      notify(`${(e as Error).message}`);
    } finally {
      setSending(null);
      inputRef.current?.focus();
    }
  };

  const onKeyDown = (e: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey || e.altKey) return;
    if (e.nativeEvent.isComposing || e.keyCode === 229) return; // 输入法选字
    e.preventDefault();
    submit();
  };

  const lastAi = [...turns].reverse().find((t) => t.role === "ai");

  return (
    <div className="ai-chat">
      {(turns.length > 0 || busy) && (
        <div className="ai-chat-list" ref={listRef}>
          {turns.map((t) =>
            t.role === "staff" ? (
              <div key={t.id} className="ai-chat-staff">
                <span>{t.content}</span>
              </div>
            ) : (
              <div key={t.id} className={`ai-chat-ai ${t.id === lastAi?.id ? "latest" : ""}`}>
                <p>{t.content}</p>
                {t.note && <p className="ai-chat-note">{t.note}</p>}
                <div className="ai-chat-tools">
                  <CopyButton text={t.content} label="复制" title="复制 AI 这次写的回复" />
                  {filledId === t.id ? (
                    <span className="filled">已填入输入框</span>
                  ) : (
                    <button
                      className={`link ${t.id === lastAi?.id ? "strong" : ""}`}
                      onClick={() => {
                        onFill(t.content);
                        onFilled(t.id);
                      }}
                    >
                      填入输入框
                    </button>
                  )}
                </div>
              </div>
            ),
          )}
          {sending && (
            <div className="ai-chat-staff">
              <span>{sending}</span>
            </div>
          )}
          {sending && <div className="ai-chat-ai pending">AI 正在写…</div>}
        </div>
      )}
      <div className="ai-chat-input">
        <textarea
          ref={inputRef}
          value={input}
          rows={Math.min(4, Math.max(2, input.split("\n").length))}
          maxLength={2000}
          placeholder={
            turns.length
              ? "还要怎么改？比如：语气再委婉些、加上让客户发日志"
              : "说说你的处理方案或要点，AI 帮你写成发给客户的回复"
          }
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button className="primary" disabled={busy || !input.trim()} onClick={submit}>
          {busy ? "写作中…" : "发给 AI"}
        </button>
      </div>
    </div>
  );
}
