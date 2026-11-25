import { useState } from "react";
import type { Correction, CourseCitation } from "./model";
import Markdown from "react-markdown";
import gfm from "remark-gfm";
import math from "remark-math";
import katex from "rehype-katex";
import highlight from "rehype-highlight";
import "katex/dist/katex.min.css";
import "highlight.js/styles/github.css";

export function CourseText({ text }: { text: string }) {
  return (
    <Markdown remarkPlugins={[gfm, math]} rehypePlugins={[katex, highlight]}>
      {text}
    </Markdown>
  );
}

function SourceCard({ hit }: { hit: CourseCitation }) {
  const [expanded, setExpanded] = useState(false);
  const long = hit.snippet.length > 200 || hit.snippet.split("\n").length > 7;
  const preview = hit.snippet
    .split(/\n\s*\n|(?<=[。！？])\s*/u)
    .find((part) => part.trim() && !/^\s*#{1,6}\s+[^\n]*$/.test(part));
  return (
    <section className="source-card">
      <header>
        <strong>
          {hit.doc_title} · 片段 #{hit.chunk_index}
        </strong>
        <small>
          {hit.score === undefined
            ? "本地课件"
            : "相关度 " + hit.score.toFixed(2)}
        </small>
      </header>
      <div className={long && !expanded ? "source-preview" : ""}>
        <CourseText
          text={long && !expanded ? preview || hit.snippet : hit.snippet}
        />
      </div>
      {long && (
        <button
          type="button"
          className="quiet"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起片段" : "展开全文"}
        </button>
      )}
    </section>
  );
}
export function MessageSources({ citations }: { citations: CourseCitation[] }) {
  return (
    <details className="source-list">
      <summary>参考资料片段（共 {citations.length} 条）</summary>
      {citations.map((hit) => <SourceCard key={hit.chunk_index} hit={hit} />)}
    </details>
  );
}

export function CorrectionDetails({ card }: { card: Correction }) {
  return <>
    <h4>你的表述</h4><blockquote>{card.user_claim}</blockquote>
    <h4>Agent 认知文档标准解读与举例</h4><CourseText text={card.accurate_explanation} />
    <h4>参考依据</h4><CourseText text={card.item_statement} />
    {card.citations.map(hit => "kind" in hit
      ? <small key={"item-" + hit.item_id}>{hit.section_title} · 认知条目 #{hit.item_id}</small>
      : <SourceCard key={hit.chunk_index} hit={hit} />)}
  </>;
}
