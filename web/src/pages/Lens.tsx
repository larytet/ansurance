import { type KeyboardEvent, type PointerEvent, type WheelEvent, useRef } from "react";
import { useUi } from "../ui";

/** Items shown in full at once. The spec's example is "41-60 of 312": a window of twenty. */
export const LENS_SIZE = 20;

export const clampStart = (start: number, n: number, size = LENS_SIZE): number =>
  Math.max(0, Math.min(start, Math.max(0, n - size)));

/** The strip above a long list: one thin bar per item (red urgent, amber important) and a
 * draggable window over the ones shown in full. Drag it, click a bar, use the arrow and page
 * keys, or scroll over the strip. */
export function LensStrip({ urgencies, start, size = LENS_SIZE, onChange }: {
  urgencies: string[]; start: number; size?: number; onChange: (start: number) => void;
}) {
  const { tr } = useUi();
  const strip = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const n = urgencies.length;
  const at = (e: PointerEvent): void => {
    const el = strip.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || !Number.isFinite(e.clientX)) return;
    const rtl = getComputedStyle(el).direction === "rtl";
    const x = e.clientX - r.left;
    const f = rtl ? 1 - x / r.width : x / r.width;
    onChange(clampStart(Math.round(f * n - size / 2), n, size));
  };
  const move = (d: number): void => onChange(clampStart(start + d, n, size));
  const key = (e: KeyboardEvent): void => {
    const d = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : e.key === "PageDown" ? size : e.key === "PageUp" ? -size : 0;
    if (d !== 0) { move(d); e.preventDefault(); }
  };
  const wheel = (e: WheelEvent): void => { move(e.deltaY > 0 ? 1 : -1); };
  const last = Math.min(n, start + size);
  return (
    <div>
      <div
        ref={strip}
        className="strip"
        role="slider"
        tabIndex={0}
        aria-label={tr("lens")}
        aria-valuemin={1}
        aria-valuemax={Math.max(1, n - size + 1)}
        aria-valuenow={start + 1}
        aria-valuetext={`${start + 1}-${last} ${tr("of")} ${n}`}
        onKeyDown={key}
        onWheel={wheel}
        onPointerDown={(e) => { dragging.current = true; try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* not supported here */ } at(e); }}
        onPointerMove={(e) => { if (dragging.current) at(e); }}
        onPointerUp={() => { dragging.current = false; }}
      >
        {urgencies.map((u, i) => <span key={i} className={`bar ${u}`} />)}
        <span className="lens" data-testid="lens" style={{ insetInlineStart: `${(start / n) * 100}%`, width: `${Math.min(1, size / n) * 100}%` }} />
      </div>
      <p className="lensinfo"><span data-testid="lens-window">{start + 1}-{last} {tr("of")} {n}</span> <small>{tr("lensHint")}</small></p>
    </div>
  );
}
