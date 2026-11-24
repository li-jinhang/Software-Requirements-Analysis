export type Concept = {
  id: string;
  name: string;
  summary: string;
  score: number;
  importance: number;
  note: string;
  syncedNote?: string;
  streak: number;
  mastery?: "weak" | "unlearned" | "learning" | "mastered";
  points?: string[];
};
export type Link = { from: string; to: string; label: string };
export type Course = {
  id: string;
  title: string;
  subject: string;
  format: string;
  cover: number;
  parseStatus?: string;
  error?: string;
  nodes: Concept[];
  links: Link[];
};
export type CognitiveItem = {
  id: number;
  item_order: number;
  section_title: string;
  statement: string;
  key_criteria: string;
  status: "untested" | "wrong" | "partial" | "mastered";
  user_evidence: string;
  issue_note: string;
};
export type CognitiveUpdate = {
  items: CognitiveItem[];
  current_item_id: number | null;
  mastery_score: number;
  mastered_count: number;
};
export type CognitiveDoc = Omit<CognitiveUpdate, "items"> & {
  doc_id: number;
  overview: string;
  total: number;
  sections: { title: string; items: CognitiveItem[] }[];
};
export type Correction = {
  proactive: true;
  issue_type: "wrong" | "inaccurate" | "vague";
  label: string;
  concept_name: string;
  item_id: number;
  item_statement: string;
  user_claim: string;
  accurate_explanation: string;
  citations: (CourseCitation | {kind: "cognitive_item"; item_id: number; section_title: string})[];
};
export type Message = {
  correction?: Correction;
  id?: number;
  who: "我" | "听众" | "百科" | "系统";
  text: string;
  node?: string;
  badge?: string;
  citation?: string;
  excerpt?: string;
  citations?: CourseCitation[];
};
export type CourseCitation = {
  doc_title: string;
  chunk_index: number;
  snippet: string;
  score?: number;
};
export type Session = {
  id: string;
  doc: string;
  focus: string;
  role: string;
  left: Message[];
  right: Message[];
  drafts: [string, string];
  closed: boolean;
};
export type Weak = {
  id: string;
  doc: string;
  node: string;
  detail: string;
  resolved: boolean;
  session: string;
};
export type Report = {
  sessionId?: string;
  id: string;
  doc: string;
  title: string;
  date: string;
  scores: number[];
  weaknesses: string[];
  plan: string[];
  comment?: string;
};
export type Space = {
  courses: Course[];
  sessions: Session[];
  reports: Report[];
  logs: Weak[];
};
export const uid = () => crypto.randomUUID();
export const roles = ["启蒙小学生", "互助同窗", "严苛考官"];
export const mean = (nums: number[]) =>
  Math.round(nums.reduce((a, b) => a + b, 0) / (nums.length || 1));
export function course(
  title: string,
  subject: string,
  format: string,
  cover: number,
  rows: [string, string, number][],
  edges: [number, number, string][] = [],
): Course {
  const nodes = rows.map(([name, summary, score], i) => ({
    id: uid(),
    name,
    summary,
    score,
    importance: i === 0 ? 5 : 3,
    note: "",
    streak: 0,
  }));
  return {
    id: uid(),
    title,
    subject,
    format,
    cover,
    nodes,
    links: edges.map(([a, b, label]) => ({
      from: nodes[a].id,
      to: nodes[b].id,
      label,
    })),
  };
}
export function initial(): Space {
  return {
    courses: [
      course(
        "机器学习基础",
        "人工智能",
        "MD",
        0,
        [
          [
            "梯度下降",
            "梯度下降沿损失函数负梯度方向更新参数。更新规则为 $w_{t+1}=w_t-η∇L(w_t)$。",
            84,
          ],
          [
            "学习率",
            "学习率决定每次更新的步长。学习率过大可能震荡或发散，过小则收敛缓慢。",
            35,
          ],
          [
            "损失函数",
            "损失函数衡量模型预测与真实目标之间的偏差。训练通过最小化损失寻找参数。",
            70,
          ],
          [
            "过拟合",
            "模型在训练集表现良好但泛化能力差。比较训练与验证误差可以发现过拟合。",
            56,
          ],
          [
            "正则化",
            "正则化在损失中加入复杂度惩罚，以约束模型并改善泛化。",
            62,
          ],
        ],
        [
          [2, 0, "优化目标"],
          [1, 0, "控制步长"],
          [4, 3, "抑制"],
          [4, 2, "修正目标"],
        ],
      ),
      course(
        "计算机网络",
        "计算机科学",
        "TXT",
        1,
        [
          [
            "分层协议",
            "网络各层通过接口提供服务，协议规定同层实体的通信规则。",
            80,
          ],
          ["可靠传输", "序号、确认和重传机制使接收方可以有序交付数据。", 43],
          [
            "拥塞控制",
            "发送方根据网络拥塞信号调整拥塞窗口，改变发送速率。",
            30,
          ],
        ],
        [
          [0, 1, "包含"],
          [0, 2, "包含"],
        ],
      ),
      course(
        "数据结构与算法",
        "计算机科学",
        "MD",
        2,
        [
          ["二叉树", "二叉树的每个节点最多有两个子节点。", 90],
          ["树的遍历", "前序、中序和后序遍历由访问根节点的先后位置区分。", 65],
          [
            "二叉搜索树",
            "左子树键值小于根，右子树键值大于根。在树高为 h 时查找复杂度为 O(h)。",
            42,
          ],
        ],
        [
          [0, 1, "应用"],
          [0, 2, "特化"],
        ],
      ),
    ],
    sessions: [],
    reports: [],
    logs: [],
  };
}
export const status = (n: Concept, logs: Weak[]) =>
  n.mastery
    ? (
        {
          weak: "待补漏",
          unlearned: "未学习",
          learning: "巩固中",
          mastered: "已掌握",
        } as const
      )[n.mastery]
    : logs.some((w) => w.node === n.id && !w.resolved) || n.score < 40
      ? "待补漏"
      : n.score < 80
        ? "巩固中"
        : "已掌握";
export function download(name: string, body: BlobPart, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function archive(c: Course, logs: Weak[], json: boolean) {
  const weaknesses = logs.filter((w) => w.doc === c.id);
  const name = (id: string) => c.nodes.find((n) => n.id === id)?.name;
  const sections = [
    [
      "核心概念树",
      ...c.nodes.map(
        (n) =>
          "- " +
          n.name +
          "（" +
          n.score +
          "%） " +
          n.summary +
          (n.note ? " · 笔记 " + n.note : ""),
      ),
    ],
    [
      "概念关联",
      ...c.links.map(
        (l) => "- " + name(l.from) + " → " + name(l.to) + " · " + l.label,
      ),
    ],
    [
      "已掌握项",
      ...c.nodes
        .filter((n) => status(n, logs) === "已掌握")
        .map((n) => "- " + n.name),
    ],
    [
      "薄弱点清单",
      ...weaknesses.map(
        (w) => "- " + w.detail + (w.resolved ? "（已解决）" : ""),
      ),
    ],
  ];
  download(
    c.title + "-" + Date.now() + (json ? ".json" : ".md"),
    json
      ? JSON.stringify({ ...c, weaknesses }, null, 2)
      : "# " +
          c.title +
          "\n\n" +
          sections
            .map(
              ([h, ...items]) =>
                "## " + h + "\n" + (items.join("\n") || "暂无"),
            )
            .join("\n\n"),
  );
}
export function newSession(
  id: string,
  c: Course,
  n: Concept,
  role: string,
): Session {
  return {
    id,
    doc: c.id,
    focus: n.id,
    role,
    left: [],
    right: [],
    drafts: ["", ""],
    closed: false,
  };
}
const dimensions = ["知识覆盖度", "概念准确度", "表达通俗度", "逻辑严密性"];
export { dimensions };
