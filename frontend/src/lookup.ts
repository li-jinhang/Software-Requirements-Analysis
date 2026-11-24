import type { Course, Message } from "./model";

// Keep the listener's follow-up attached to the explanation that prompted it,
// including when a student revisits an older turn.
export function lookupQuestion(left: Message[], index: number, course: Course) {
  const followup = left[index].text;
  const lecture = left
    .slice(0, index)
    .reverse()
    .find((m) => m.who === "我");
  const topic = followup.match(/「([^」]+)」/)?.[1] || "";
  const ask = lecture?.text
    .split(/(?<=[。！？!?])|\n/)
    .map((s) => s.trim())
    .filter((s) => /[？?]|举例|举个|公式|类比/.test(s))
    .slice(-2)
    .join("");
  if (ask) {
    // A student's own specific question takes precedence over a generic follow-up.
    const concepts = course.nodes
      .map((n) => n.name)
      .sort((a, b) => b.length - a.length);
    const subject =
      concepts.find((name) => lecture?.text.includes(name)) || topic;
    return (
      subject && !concepts.some((name) => ask.includes(name))
        ? subject + "：" + ask
        : ask
    ).slice(0, 400);
  }
  const intent = followup
    .replace(/「[^」]+」/g, "")
    .replace(/^[:：，。\s]+/, "");
  if (/比方|类比|例子|举例/.test(intent)) return topic + "如何用具体例子说明？";
  if (/反例|条件|边界/.test(intent)) return topic + "的适用条件和反例是什么？";
  if (/比较|对比|不同|易混/.test(intent)) return topic + "与相关概念有何区别？";
  return topic ? topic + "的核心含义是什么？" : followup.slice(0, 400);
}
