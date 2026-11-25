import type { CSSProperties } from "react";
import { Circle, Crown, Flame, Leaf, Snowflake, Sparkles, Waves, Zap } from "lucide-react";
import "./mastery-progress.css";

const bands = [
  { tone: "empty", max: 0, name: "未激活", icon: Circle },
  { tone: "start", max: 20, name: "初启", icon: Sparkles },
  { tone: "ice", max: 40, name: "冰封", icon: Snowflake },
  { tone: "water", max: 60, name: "流动", icon: Waves },
  { tone: "growth", max: 75, name: "生长", icon: Leaf },
  { tone: "charge", max: 90, name: "冲刺", icon: Zap },
  { tone: "fire", max: 100, name: "燃烧完成", icon: Flame },
] as const;

export function masteryForScore(score: number) {
  const value = Number.isFinite(score) ? Math.min(100, Math.max(0, Math.trunc(score))) : 0;
  const band = bands.find((entry) => value <= entry.max) ?? bands[bands.length - 1];
  return { value, ...band };
}

type MeterProps = {
  score: number;
  label: string;
  status?: string;
  compact?: boolean;
  motion?: "always" | "focus";
};

export function MasteryMeter({ score, label, status, compact, motion = "always" }: MeterProps) {
  const { tone, value, icon } = masteryForScore(score);
  const TipIcon = value === 100 ? Crown : icon;
  return (
    <span className={compact ? "mastery-meter is-compact" : "mastery-meter"}
      data-mastery={tone} data-motion={motion}
      role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value}
      aria-valuetext={status ? `${status} · ${value}%` : `${value}%`}
      style={{ "--mastery-value": `${value}%` } as CSSProperties}>
      <span className="mastery-track" aria-hidden="true">
        {value > 0 && <span className="mastery-fill" />}
      </span>
      {value > 0 && (
        <span className="mastery-tip" aria-hidden="true">
          <TipIcon className="mastery-tip-icon" />
        </span>
      )}
    </span>
  );
}

export function MasteryProgress({ score, status, label = "知识点掌握进度" }: {
  score: number;
  status?: string;
  label?: string;
}) {
  const { value } = masteryForScore(score);
  return (
    <div className="mastery-progress">
      <div className="mastery-readout">
        <span>{status || "掌握度"}</span>
        <b>{value}%</b>
      </div>
      <MasteryMeter score={score} label={label} status={status} />
    </div>
  );
}
