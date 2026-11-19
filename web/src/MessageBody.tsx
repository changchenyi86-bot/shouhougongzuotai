import { useState } from "react";
import { attachmentUrl, fetchMedia, type Attachment, type Message } from "./api";

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const isImage = (a: Attachment) => a.mime.startsWith("image/");

export function AttachmentView({ att }: { att: Attachment }) {
  if (isImage(att))
    return (
      <a className="att-img" href={attachmentUrl(att.id)} target="_blank" rel="noreferrer" title="点击查看原图">
        <img src={attachmentUrl(att.id)} alt={att.filename} loading="lazy" />
      </a>
    );
  return (
    <a className="att-file" href={attachmentUrl(att.id, true)} title="下载文件">
      <span className="att-icon">{(att.filename.split(".").pop() ?? "").slice(0, 4).toUpperCase() || "文件"}</span>
      <span className="att-meta">
        <b>{att.filename}</b>
        <small>{formatSize(att.size)} · 点击下载</small>
      </span>
    </a>
  );
}

/** 聊天气泡里的内容：文字，或客户发来的图片/文件（下载失败时可重试）。 */
export default function MessageBody({ message: m, onChanged }: { message: Message; onChanged: () => void }) {
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (m.attachment) return <AttachmentView att={m.attachment} />;
  if (m.msg_type === "text") return <div className="bubble">{m.content}</div>;

  const retry = async () => {
    setRetrying(true);
    try {
      const fresh = await fetchMedia(m.id);
      setError(fresh.attachment ? null : fresh.media_error);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRetrying(false);
    }
  };
  const err = error ?? m.media_error;
  return (
    <div className="bubble media">
      {m.content}
      {err && (
        <div className="media-error">
          下载失败：{err}
          <button className="link" disabled={retrying} onClick={retry}>{retrying ? "下载中…" : "重新下载"}</button>
        </div>
      )}
    </div>
  );
}
