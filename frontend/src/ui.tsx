import React, { useEffect, useRef } from "react";
import { X, Activity, Network, GitFork, FileText } from "lucide-react";
export function Dialog({
  title,
  close,
  children,
  drawer = false,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
  drawer?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current!;
    const opener = document.activeElement as HTMLElement;
    el.showModal();
    return () => {
      el.close();
      opener?.focus();
    };
  }, []);
  return (
    <dialog
      className={drawer ? "drawer" : ""}
      ref={ref}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
      onClick={(e) => {
        if (e.target === ref.current) close();
      }}
      aria-label={title}
    >
      <div className="dialog-content">
        <header className="row between">
          <h2>{title}</h2>
          <button onClick={close} aria-label="关闭">
            <X />
          </button>
        </header>
        {children}
      </div>
    </dialog>
  );
}
export function Diagram({ type }: { type: number }) {
  const icon = [Activity, Network, GitFork, FileText][type] || FileText;
  const Icon = icon;
  return (
    <div className={"cover cover" + type}>
      <Icon size={21} />
      <svg aria-hidden="true" viewBox="0 0 230 90" fill="none">
        {type === 0 ? (
          <g stroke="currentColor">
            {Array.from({ length: 12 }, (_, i) => (
              <path
                key={i}
                d={`M ${-30 + i * 7} 0 Q ${65 + i * 2} ${180 - i * 5} ${210 + i * 7} -10`}
              />
            ))}
          </g>
        ) : type === 1 ? (
          <g stroke="currentColor">
            {[0, 1, 2, 3].map((i) => (
              <g key={i}>
                <path
                  d={`M25 45L90 ${10 + i * 22}L165 45L220 ${10 + i * 22}`}
                />
                <circle cx="90" cy={10 + i * 22} r="3" />
                <circle cx="220" cy={10 + i * 22} r="3" />
              </g>
            ))}
          </g>
        ) : type === 2 ? (
          <g stroke="currentColor">
            {[0, 1, 2, 3].map((i) => (
              <g key={i}>
                <path
                  d={`M116 5V18H${60 + (i >> 1) * 110}V36H${30 + i * 55}V65`}
                />
                <rect x={23 + i * 55} y="65" width="14" height="10" />
              </g>
            ))}
          </g>
        ) : (
          <g stroke="currentColor">
            {[15, 30, 45, 60].map((y, i) => (
              <path key={y} d={`M45 ${y}h${135 - i * 12}`} />
            ))}
          </g>
        )}
      </svg>
    </div>
  );
}
