import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { attachmentUrl, type Attachment } from "./api";

type Open = (att: Attachment, gallery?: Attachment[]) => void;

const ViewerCtx = createContext<Open | null>(null);
/** 当前页面上可以左右切换的一组图片（如一个群的聊天记录里的全部图片）。 */
const GalleryCtx = createContext<Attachment[]>([]);

export function ImageGallery({ images, children }: { images: Attachment[]; children: ReactNode }) {
  return <GalleryCtx.Provider value={images}>{children}</GalleryCtx.Provider>;
}

/** 点图片时调用：在当前页面弹出浮窗看大图。gallery 不传时用所在的 ImageGallery。 */
export function useImageViewer(): Open {
  const open = useContext(ViewerCtx);
  const gallery = useContext(GalleryCtx);
  return useCallback<Open>(
    (att, list) => {
      const images = list ?? gallery;
      open?.(att, images.some((a) => a.id === att.id) ? images : [att]);
    },
    [open, gallery],
  );
}

export function ImageViewerProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ images: Attachment[]; index: number } | null>(null);
  const open = useCallback<Open>((att, gallery = [att]) => {
    setState({ images: gallery, index: Math.max(0, gallery.findIndex((a) => a.id === att.id)) });
  }, []);
  return (
    <ViewerCtx.Provider value={open}>
      {children}
      {state &&
        createPortal(
          <Viewer
            images={state.images}
            index={state.index}
            onIndex={(index) => setState((s) => s && { ...s, index })}
            onClose={() => setState(null)}
          />,
          document.body,
        )}
    </ViewerCtx.Provider>
  );
}

const MIN = 0.1;
const MAX = 10;
const STEP = 1.25;
const clamp = (s: number) => Math.min(MAX, Math.max(MIN, s));

type View = { s: number; x: number; y: number };
const FIT: View = { s: 1, x: 0, y: 0 };

function Viewer({ images, index, onIndex, onClose }: {
  images: Attachment[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  const att = images[index];
  const [view, setView] = useState<View>(FIT);
  // 记的是哪张图加载好了 / 失败了，切换图片时不用另外重置
  const [loadedId, setLoadedId] = useState<string | null>(null);
  const [failedId, setFailedId] = useState<string | null>(null);
  const loaded = loadedId === att.id;
  const failed = failedId === att.id;
  const img = useRef<HTMLImageElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  // 一次按下到松开：从哪按下、动没动过、是不是按在图片外的空白处、双指间距
  const gesture = useRef({ sx: 0, sy: 0, moved: false, onBackdrop: false, dist: undefined as number | undefined });
  const closeBtn = useRef<HTMLButtonElement>(null);

  const go = useCallback(
    (d: number) => {
      const next = index + d;
      if (next >= 0 && next < images.length) onIndex(next);
    },
    [index, images.length, onIndex],
  );

  useEffect(() => setView(FIT), [att.id]);

  /** 以屏幕上的某一点（相对画面中心）为中心缩放，鼠标下的那部分图保持不动。 */
  const zoomAt = useCallback((factor: number, px = 0, py = 0) => {
    setView((v) => {
      const s = clamp(v.s * factor);
      const k = s / v.s;
      return { s, x: px - (px - v.x) * k, y: py - (py - v.y) * k };
    });
  }, []);

  const center = (clientX: number, clientY: number) => {
    const r = stage.current!.getBoundingClientRect();
    return [clientX - r.left - r.width / 2, clientY - r.top - r.height / 2] as const;
  };

  // 原图多大：显示比例按原图算，100% 就是原图大小
  const fitRatio = () => {
    const el = img.current;
    return el && el.naturalWidth ? el.clientWidth / el.naturalWidth : 1;
  };
  const percent = loaded ? Math.round(view.s * fitRatio() * 100) : null;
  const atFit = view.s === 1 && view.x === 0 && view.y === 0;
  const toggleActual = () => setView(atFit ? { s: 1 / fitRatio(), x: 0, y: 0 } : FIT);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft") go(-1);
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "+" || e.key === "=") zoomAt(STEP);
      else if (e.key === "-") zoomAt(1 / STEP);
      else if (e.key === "0") setView(FIT);
      else return;
      // 浮窗开着时按键只给浮窗用，不再关掉下面页面上的弹层
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [go, onClose, zoomAt]);

  // 打开时把焦点放进浮窗，关闭后还给原来的元素；浮窗打开期间页面不滚动
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    closeBtn.current?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = overflow;
      before?.focus?.();
    };
  }, []);

  // 滚轮缩放要能阻止页面默认滚动，React 的 onWheel 是被动监听，这里直接绑
  useEffect(() => {
    const el = stage.current!;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const [px, py] = center(e.clientX, e.clientY);
      // 触控板双指捏合会带 ctrlKey，幅度更细
      const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0025));
      zoomAt(factor, px, py);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const down = (e: ReactPointerEvent) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button, a")) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    stage.current!.setPointerCapture(e.pointerId);
    if (pointers.current.size === 1)
      gesture.current = { sx: e.clientX, sy: e.clientY, moved: false, onBackdrop: e.target === stage.current, dist: undefined };
  };
  const move = (e: ReactPointerEvent) => {
    const last = pointers.current.get(e.pointerId);
    if (!last) return;
    const now = { x: e.clientX, y: e.clientY };
    pointers.current.set(e.pointerId, now);
    const pts = [...pointers.current.values()];
    if (pts.length === 2) {
      // 双指捏合缩放
      const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      const prev = gesture.current.dist;
      gesture.current = { ...gesture.current, moved: true, dist };
      if (prev) {
        const [px, py] = center((pts[0].x + pts[1].x) / 2, (pts[0].y + pts[1].y) / 2);
        zoomAt(dist / prev, px, py);
      }
      return;
    }
    const g = gesture.current;
    if (!g.moved) {
      if (Math.hypot(now.x - g.sx, now.y - g.sy) < 3) return; // 手抖不算拖动
      g.moved = true;
    }
    const dx = now.x - last.x;
    const dy = now.y - last.y;
    setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
  };
  const up = (e: ReactPointerEvent) => {
    if (!pointers.current.delete(e.pointerId)) return;
    if (pointers.current.size < 2) gesture.current.dist = undefined;
    // 点图片以外的空白处关闭；拖动、捏合之后松手不算
    if (pointers.current.size === 0 && !gesture.current.moved && gesture.current.onBackdrop) onClose();
  };

  const many = images.length > 1;
  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label={`查看图片：${att.filename}`}>
      <div className="viewer-top">
        <span className="viewer-name" title={att.filename}>{att.filename}</span>
        {many && <span className="viewer-count">{index + 1} / {images.length}</span>}
        <button ref={closeBtn} className="viewer-btn viewer-close" onClick={onClose} title="关闭（Esc）" aria-label="关闭">
          <svg viewBox="0 0 20 20" aria-hidden><path d="M5 5l10 10M15 5L5 15" /></svg>
        </button>
      </div>

      <div
        ref={stage}
        className="viewer-stage"
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
        onDoubleClick={(e) => {
          const [px, py] = center(e.clientX, e.clientY);
          if (atFit) zoomAt(2, px, py);
          else setView(FIT);
        }}
      >
        {!loaded && !failed && <span className="viewer-note">加载中…</span>}
        {failed && <span className="viewer-note">图片加载失败，可以下载后查看</span>}
        <img
          key={att.id}
          ref={img}
          src={attachmentUrl(att.id)}
          alt={att.filename}
          draggable={false}
          onLoad={() => setLoadedId(att.id)}
          onError={() => setFailedId(att.id)}
          style={{
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})`,
            visibility: loaded ? "visible" : "hidden",
          }}
        />
        {many && (
          <>
            <button className="viewer-btn viewer-nav prev" disabled={index === 0} onClick={() => go(-1)} title="上一张（←）" aria-label="上一张">
              <svg viewBox="0 0 20 20" aria-hidden><path d="M12.5 4.5 7 10l5.5 5.5" /></svg>
            </button>
            <button className="viewer-btn viewer-nav next" disabled={index === images.length - 1} onClick={() => go(1)} title="下一张（→）" aria-label="下一张">
              <svg viewBox="0 0 20 20" aria-hidden><path d="M7.5 4.5 13 10l-5.5 5.5" /></svg>
            </button>
          </>
        )}
      </div>

      <div className="viewer-bar">
        <button className="viewer-btn" onClick={() => zoomAt(1 / STEP)} disabled={view.s <= MIN} title="缩小（-）" aria-label="缩小">
          <svg viewBox="0 0 20 20" aria-hidden><circle cx="9" cy="9" r="5.5" /><path d="M6.5 9h5M13 13l3.5 3.5" /></svg>
        </button>
        <button className="viewer-pct" onClick={toggleActual} title={atFit ? "原始大小" : "适应窗口（0）"}>
          {percent !== null ? `${percent}%` : "—"}
        </button>
        <button className="viewer-btn" onClick={() => zoomAt(STEP)} disabled={view.s >= MAX} title="放大（+）" aria-label="放大">
          <svg viewBox="0 0 20 20" aria-hidden><circle cx="9" cy="9" r="5.5" /><path d="M6.5 9h5M9 6.5v5M13 13l3.5 3.5" /></svg>
        </button>
        <span className="viewer-sep" />
        <button className="viewer-btn" onClick={() => setView(FIT)} disabled={atFit} title="适应窗口（0）" aria-label="适应窗口">
          <svg viewBox="0 0 20 20" aria-hidden><path d="M3.5 7.5v-4h4M16.5 7.5v-4h-4M3.5 12.5v4h4M16.5 12.5v4h-4" /></svg>
        </button>
        <a className="viewer-btn" href={attachmentUrl(att.id, true)} title="下载" aria-label="下载">
          <svg viewBox="0 0 20 20" aria-hidden><path d="M10 3.5v9M6 9l4 4 4-4M4 16.5h12" /></svg>
        </a>
      </div>
    </div>
  );
}
