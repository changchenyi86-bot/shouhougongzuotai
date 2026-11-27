import { useState, type ReactNode } from "react";
import { attachmentUrl, fetchMedia, type Attachment, type Message } from "./api";
import CopyButton from "./CopyButton";
import { useImageViewer } from "./ImageViewer";

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export const isImage = (a: Attachment) => a.mime.startsWith("image/");

type FileKind = "excel" | "word" | "ppt" | "pdf" | "zip" | "image" | "video" | "audio" | "text" | "other";

const EXT: Record<Exclude<FileKind, "other">, string[]> = {
  excel: ["xls", "xlsx", "xlsm", "xlsb", "csv", "et", "ett", "numbers"],
  word: ["doc", "docx", "docm", "dot", "dotx", "wps", "wpt", "rtf", "pages"],
  ppt: ["ppt", "pptx", "pptm", "pps", "ppsx", "dps", "dpt", "key"],
  pdf: ["pdf"],
  zip: ["zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz"],
  image: ["png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "heic", "tif", "tiff"],
  video: ["mp4", "mov", "avi", "mkv", "wmv", "flv", "m4v"],
  audio: ["mp3", "wav", "m4a", "amr", "aac", "flac", "ogg"],
  text: ["txt", "log", "md", "json", "xml", "yaml", "yml", "sql", "ini", "conf", "properties", "py", "java", "html"],
};
const KIND_OF = new Map(Object.entries(EXT).flatMap(([k, exts]) => exts.map((e) => [e, k as FileKind])));

const ext = (name: string) => (name.includes(".") ? name.split(".").pop()!.toLowerCase() : "");
const fileKind = (name: string): FileKind => KIND_OF.get(ext(name)) ?? "other";

// 压缩包、可执行文件、脚本、带宏的文档：客户发来时提醒谨慎下载
const RISKY = new Set([
  ...EXT.zip,
  ...["exe", "msi", "bat", "cmd", "com", "scr", "ps1", "vbs", "js", "jar", "apk", "dmg", "pkg", "iso", "sh", "hta", "lnk"],
  ...["xlsm", "docm", "pptm"],
]);
const isRisky = (name: string) => RISKY.has(ext(name));

// 文件类型的颜色和大家熟悉的办公软件图标一致，两种界面风格都不变
const ICON_COLOR: Record<FileKind, string> = {
  excel: "#20a05a",
  word: "#3b74f6",
  ppt: "#f0663b",
  pdf: "#e5484d",
  zip: "#f5a524",
  image: "#14a8c4",
  video: "#8b5cf6",
  audio: "#e2558e",
  text: "#6b7a90",
  other: "#9aa3b2",
};
const LETTER: Partial<Record<FileKind, string>> = { excel: "X", word: "W", ppt: "P" };

function Glyph({ kind }: { kind: FileKind }) {
  const white = { fill: "#fff" };
  if (LETTER[kind])
    return (
      <text x="20" y="35" textAnchor="middle" fontSize="19" fontWeight="700" fontFamily="Arial, Helvetica, sans-serif" {...white}>
        {LETTER[kind]}
      </text>
    );
  switch (kind) {
    case "pdf":
      return (
        <text x="20" y="34" textAnchor="middle" fontSize="11" fontWeight="700" fontFamily="Arial, Helvetica, sans-serif" {...white}>
          PDF
        </text>
      );
    case "zip":
      return (
        <g {...white}>
          {[0, 1, 2, 3, 4].map((i) => (
            <rect key={i} x={i % 2 ? 20 : 16} y={3 + i * 5} width="4" height="4" rx=".5" />
          ))}
          <rect x="15" y="28" width="10" height="11" rx="2" />
          <rect x="17.5" y="33" width="5" height="3" rx="1" fill={ICON_COLOR.zip} />
        </g>
      );
    case "image":
      return (
        <g {...white}>
          <circle cx="14" cy="24" r="3" />
          <path d="M8 38l8-9 5 5 4-4 7 8z" />
        </g>
      );
    case "video":
      return <path d="M16 24v14l11-7z" {...white} />;
    case "audio":
      return (
        <g {...white}>
          <path d="M18 22h9v4h-6v10.5a3.5 3.5 0 1 1-3-3.46z" />
        </g>
      );
    default:
      return (
        <g {...white}>
          <rect x="10" y="22" width="20" height="2.6" rx="1.3" />
          <rect x="10" y="28" width="20" height="2.6" rx="1.3" />
          <rect x="10" y="34" width="13" height="2.6" rx="1.3" />
        </g>
      );
  }
}

/** 文件类型图标：Excel 绿 X、Word 蓝 W、PPT 橙 P、PDF 红、压缩包黄色拉链等。 */
export function FileIcon({ name, size = 40 }: { name: string; size?: number }) {
  const kind = fileKind(name);
  return (
    <svg className="file-icon" width={size} height={size * 1.2} viewBox="0 0 40 48" aria-hidden>
      <path d="M5 0h21l14 14v29a5 5 0 0 1-5 5H5a5 5 0 0 1-5-5V5a5 5 0 0 1 5-5z" fill={ICON_COLOR[kind]} />
      <path d="M26 0l14 14h-9a5 5 0 0 1-5-5z" fill="#fff" fillOpacity=".4" />
      <Glyph kind={kind} />
    </svg>
  );
}

const DownloadIcon = () => (
  <svg className="dl-icon" viewBox="0 0 16 16" aria-hidden>
    <path d="M8 2.5v8M4.5 7 8 10.5 11.5 7M3 13.5h10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/** 文件卡片：类型图标、文件名、大小，右下角下载。没下载下来时 foot 里放原因和重试。 */
function FileCard({ name, size, href, foot }: { name: string; size?: number; href?: string; foot?: ReactNode }) {
  const body = (
    <>
      <FileIcon name={name} />
      <span className="file-info">
        <span className="file-name">{name}</span>
        <span className="file-foot">
          {foot ?? (
            <>
              <small>{size !== undefined ? formatSize(size) : ""}</small>
              <DownloadIcon />
            </>
          )}
        </span>
      </span>
    </>
  );
  return href ? (
    <a className="file-card" href={href} title="下载文件">
      {body}
    </a>
  ) : (
    <div className="file-card">{body}</div>
  );
}

export function AttachmentView({ att }: { att: Attachment }) {
  const view = useImageViewer();
  if (isImage(att))
    return (
      <button type="button" className="att-img" onClick={() => view(att)} title="点击查看大图">
        <img src={attachmentUrl(att.id)} alt={att.filename} loading="lazy" />
      </button>
    );
  return <FileCard name={att.filename} size={att.size} href={attachmentUrl(att.id, true)} />;
}

const RiskTip = () => (
  <div className="risk-tip">
    <svg viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 4.8v3.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="8" cy="11.2" r=".85" fill="currentColor" />
    </svg>
    他人发送的文件可能存在风险，请谨慎下载
  </div>
);

/** 聊天气泡里的内容：文字，或群里发来的图片/文件（下载失败时可重试）。 */
export default function MessageBody({ message: m, onChanged }: { message: Message; onChanged: () => void }) {
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (m.attachment) {
    const tip = m.from_customer && !isImage(m.attachment) && isRisky(m.attachment.filename);
    return (
      <>
        <AttachmentView att={m.attachment} />
        {tip && <RiskTip />}
      </>
    );
  }
  if (m.msg_type === "text")
    return (
      <div className="bubble-line">
        <div className="bubble">{m.content}</div>
        <CopyButton text={m.content} title="复制这条消息" />
      </div>
    );

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
  const retryButton = (
    <button className="link" disabled={retrying} onClick={retry}>
      {retrying ? "下载中…" : "重新下载"}
    </button>
  );

  // 文件还没下载下来（下载中或失败）：也按文件卡片显示，文件名取自消息内容「[文件] 文件名」
  const fileName = m.msg_type === "file" ? m.content.replace(/^\[文件\]\s*/, "").trim() : "";
  if (fileName)
    return (
      <FileCard
        name={fileName}
        foot={err ? <span className="file-error">{err}{retryButton}</span> : <small>正在下载…</small>}
      />
    );

  return (
    <div className="bubble media">
      {m.content}
      {err && (
        <div className="media-error">
          下载失败：{err}
          {retryButton}
        </div>
      )}
    </div>
  );
}
