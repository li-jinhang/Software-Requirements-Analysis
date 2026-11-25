import type { CSSProperties } from "react";
import { Check } from "lucide-react";
import "./mastery-progress.css";

const bands = [
  { tone: "empty", max: 0, range: "0%" },
  { tone: "low", max: 39, range: "1–39%" },
  { tone: "mid", max: 79, range: "40–79%" },
  { tone: "high", max: 99, range: "80–99%" },
  { tone: "full", max: 100, range: "100%" },
] as const;

export function masteryForScore(score: number) {
  const value = Number.isFinite(score) ? Math.min(100, Math.max(0, Math.trunc(score))) : 0;
  const band = bands.find((entry) => value <= entry.max) ?? bands[4];
  return { value, tone: band.tone };
}

export function MasteryLegend() {
  return (
    <ul className="mastery-legend" aria-label="掌握进度分档">
      {bands.map(({ tone, range }) => (
        <li key={tone} data-mastery={tone}><span aria-hidden="true" />{range}</li>
      ))}
    </ul>
  );
}

export function MasteryProgress({ score, status }: { score: number; status: string }) {
  const { tone, value } = masteryForScore(score);
  const label = `${status} · ${value}%`;
  return (
    <div className="mastery-progress" data-mastery={tone}>
      <b>{label}</b>
      <div className="mastery-meter" role="progressbar" aria-label="知识点掌握进度"
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={value} aria-valuetext={label}
        style={{ "--mastery-value": `${value}%` } as CSSProperties}>
        <span className="mastery-track" aria-hidden="true"><span className="mastery-fill" /></span>
        <span className="mastery-mark" aria-hidden="true">{tone === "full" && <Check />}</span>
      </div>
    </div>
  );
}
