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

function example(question: string, topic: string) {
  const subject = question + topic;
  if (/交叉熵/.test(subject))
    return "对一个分类样本，模型给正确类别的概率为 $p$，交叉熵损失为 $-\\ln p$。取 $p=0.5$ 时约为 0.693；更自信地猜错会受到更大的惩罚。";
  if (/学习率|步长|梯度下降/.test(subject))
    return "把损失想成山谷。取 $L(w)=w^2$，从 $w=1$ 出发，梯度为 $2w$。学习率为 0.75 时，两步后依次到 −0.5 和 0.25；改成 1.1，就会走到 −1.2 和 1.44。两者都震荡，只有后者离谷底越来越远。";
  if (/损失|均方|MSE/i.test(subject))
    return "用均方误差 $\\mathrm{MSE}=\\frac1n\\sum_i(\\hat y_i-y_i)^2$ 举例：真实值为 2、4，预测为 3、2，均方误差为 $(1^2+(-2)^2)/2=2.5$。直接平均带符号误差会发生抵消。";
  if (/过拟合|正则化/.test(subject))
    return "设模型甲训练准确率 99%、验证准确率 72%，乙分别为 94%、89%。只看训练成绩容易选错；在相同验证集上比较，更能判断谁学到了可推广的规律。";
  return "用一对情形检验理解：先给出符合定义的例子，再只改一个关键条件，看结论是否改变。不要把一次观察到的现象当成普遍规律。";
}

export function localLookup(
  course: Course,
  question: string,
  focusId?: string,
): Message {
  const [lecture, followup = ""] = question.split("\n听众追问：");
  function overlap(text: string, query: string) {
    const pairs = new Set(
      Array.from(query.matchAll(/(?=([\u3400-\u9fff]{2}))/g), (m) => m[1]),
    );
    const words = new Set(query.toLowerCase().match(/[a-z0-9]+/g) || []);
    return [...pairs, ...words].filter((t) => text.toLowerCase().includes(t))
      .length;
  }
  const intent = [
    [/公式|更新规则|怎么算/, /公式|规则|\$|计算/],
    [/条件|过大|过小|步长|发散/, /条件|如果|当|过大|过小|步长|发散/],
    [/举例|例子|比喻/, /例如|比如|想象|好比/],
  ].find(([asked]) => asked.test(question));
  const ranked = course.nodes
    .flatMap((node, index) =>
      (node.summary.match(/[^。！？\n]+[。！？]?/g) || []).map((sentence) => {
        const about = node.name + sentence;
        const anchor = lecture.includes(node.name) ? 5 : 0;
        const related = course.links.some(
          (l) =>
            (l.from === focusId && l.to === node.id) ||
            (l.to === focusId && l.from === node.id),
        );
        const relevance =
          anchor +
          overlap(about, lecture) * 2 +
          overlap(about, followup) +
          (node.id === focusId ? 2 : 0) +
          (related ? 1 : 0);
        const supports = intent?.[1].test(sentence);
        return {
          node,
          index,
          sentence,
          score: relevance && relevance + (supports ? 8 : 0),
          supports,
        };
      }),
    )
    .sort((a, b) => b.score - a.score);
  const hit = ranked[0];
  const found = hit?.score ? hit : undefined;
  const source = found?.sentence || "本次没有检索到可直接引用的课件原句。";
  return {
    who: "百科",
    node: found?.node.id,
    text:
      "**课件溯源** · " +
      source +
      "\n\n**学科知识补充与举例**\n\n以下示例为基于通用学科知识的拓展补充，供你理解并转述给左侧听众。\n\n" +
      example(question, found?.node.name || "") +
      "\n\n回到左窗，用这个例子解释哪个条件决定了结果。",
    citations: found
      ? [
          {
            doc_title: course.title,
            chunk_index: found.index,
            snippet: found.node.summary,
          },
        ]
      : [],
  };
}
