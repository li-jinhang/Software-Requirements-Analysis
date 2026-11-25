import React, { createContext, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronRight, Menu, X, type LucideIcon } from "lucide-react";
export type ColorTheme = "light" | "dark";
const ToolbarTarget = createContext<HTMLDivElement | null>(null);

type FrameProps = React.PropsWithChildren<{
  title: string;
  icon: LucideIcon;
  heading: boolean;
  navOpen: boolean;
  onMenu: () => void;
}>;

export function WorkspaceFrame({ children, title, icon: Icon, heading, navOpen, onMenu }: FrameProps) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const Label = heading ? "h1" : "strong";
  return (
    <main>
      <header className="topline" aria-label="工作区顶栏">
        <button className="hamb icon-button" onClick={onMenu} aria-label="打开导航"
          aria-expanded={navOpen} aria-controls="learning-navigation">
          <Menu aria-hidden="true" />
        </button>
        <div className="workspace-location">
          <span className="crumb-space">个人空间</span>
          <ChevronRight className="crumb-divider" aria-hidden="true" />
          <Icon className="crumb-icon" aria-hidden="true" />
          <Label>{title}</Label>
        </div>
        <div className="toolbar-slot" ref={setTarget} />
      </header>
      <ToolbarTarget.Provider value={target}>{children}</ToolbarTarget.Provider>
    </main>
  );
}

// Keep page-specific controls with their data while rendering one shared header.
export function WorkspaceToolbar({ children }: React.PropsWithChildren) {
  const target = useContext(ToolbarTarget);
  return target ? createPortal(children, target) : null;
}

export function Dialog({
  title,
  close,
  children,
  drawer = false,
  summary,
  className = "",
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
  drawer?: boolean;
  summary?: React.ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current!;
    const opener = document.activeElement as HTMLElement;
    el.showModal();
    el.querySelector<HTMLElement>("[data-initial-focus]")?.focus({ preventScroll: true });
    return () => {
      el.close();
      opener?.focus();
    };
  }, []);
  return (
    <dialog
      className={[drawer && "drawer", className].filter(Boolean).join(" ")}
      ref={ref}
      onCancel={(event) => {
        event.preventDefault();
        event.stopPropagation();
        close();
      }}
      onClick={(e) => {
        if (e.target === ref.current) close();
      }}
      aria-label={title}
    >
      <div className="dialog-content">
        <header className="dialog-heading">
          <h2>{title}</h2>
          <button onClick={close} aria-label="关闭">
            <X />
          </button>
          {summary}
        </header>
        {children}
      </div>
    </dialog>
  );
}
const relationNames: Record<string, string> = {
  prerequisite: "前置依赖",
  applies_to: "应用于",
  contains: "包含",
  contrast: "对比",
};

export function relationLabel(type: string) {
  return relationNames[type] ?? type;
}
