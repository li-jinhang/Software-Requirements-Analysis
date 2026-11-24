import json
from sqlalchemy import select
from app.core.config import settings
from app.core.security import hash_password
from app.db.models import (
    User,
    Document,
    KnowledgeNode,
    KnowledgeEdge,
    Session,
    Message,
    WeaknessLog,
    DiagnosticReport,
    now,
)
from app.services import rag_store
from app.services.doc_parser import split_chunks

LESSON = [
    (
        "损失函数",
        86,
        "损失函数衡量预测值与真实标签的偏差。训练时希望减小训练损失，而评估泛化能力需要独立的验证数据。",
    ),
    (
        "梯度",
        88,
        "梯度由损失对各参数的偏导数组成，给出局部上升最快的方向；沿负梯度走一个足够小的步长，通常会降低当前损失。",
    ),
    (
        "梯度下降",
        92,
        r"梯度下降按 $w_{t+1}=w_t-\eta\nabla L(w_t)$ 更新参数。把损失曲面想成山坡，梯度决定下坡方向，学习率控制每一步走多远。",
    ),
    (
        "学习率",
        35,
        "学习率过小会使迭代缓慢，过大会在谷底两侧震荡甚至发散。可观察训练损失曲线并比较多个步长；衰减策略在训练后期减小步长。",
    ),
    (
        "过拟合",
        28,
        "当训练误差持续下降而验证误差上升，模型可能正在记忆训练样本中的噪声。这是过拟合的典型信号，应比较训练集和验证集表现。",
    ),
    (
        "正则化",
        64,
        "正则化向目标函数加入复杂度惩罚，以限制模型过度拟合。系数过大也会妨碍模型学习有效信号，因此需用验证集选择强度。",
    ),
    (
        "L1 正则化",
        46,
        "L1 惩罚取参数绝对值之和，在适当条件下会让部分系数恰好为零，适合需要稀疏权重的场景。",
    ),
    (
        "L2 正则化",
        73,
        "L2 惩罚取参数平方和，倾向于把系数平滑压小，通常不会产生精确的零值。比较 L1 和 L2 时应保持数据划分一致。",
    ),
]
EDGES = [
    (0, 1, "prerequisite"),
    (1, 2, "prerequisite"),
    (3, 2, "applies_to"),
    (0, 2, "prerequisite"),
    (5, 4, "applies_to"),
    (5, 6, "contains"),
    (5, 7, "contains"),
    (6, 7, "contrast"),
    (5, 0, "applies_to"),
]


def seed(db):
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    path = settings.data_dir / "uploads" / "demo-course.md"
    if db.scalar(select(User.id).limit(1)):
        demo = db.scalar(select(Document).where(Document.file_path == str(path),
                                                 Document.parse_status == "completed"))
        if demo and path.is_file():
            chunks = split_chunks(path.read_text(encoding="utf-8"))
            if chunks and demo.chunk_count != len(chunks):
                rag_store.remove(demo.id)
                rag_store.index(demo.id, demo.title, chunks)
                demo.chunk_count = len(chunks)
        db.commit()
        return
    admin = User(
        username="admin",
        nickname="教学管理员",
        role="admin",
        password_hash=hash_password("admin123"),
    )
    student = User(
        username="student",
        nickname="演示学生",
        role="student",
        password_hash=hash_password("123456"),
    )
    db.add_all([admin, student])
    db.flush()
    title = "机器学习基础：梯度下降与过拟合"
    text = "# " + title + "\n\n" + "\n\n".join("## " + n + "\n" + s for n, _, s in LESSON)
    path.write_text(text, encoding="utf-8")
    chunks = split_chunks(text)
    doc = Document(
        user_id=student.id,
        title=title,
        subject="机器学习",
        file_path=str(path),
        file_type="md",
        parse_status="completed",
        chunk_count=len(chunks),
        structured_summary="从损失最小化到泛化误差与正则化。",
    )
    db.add(doc)
    db.flush()
    nodes = [
        KnowledgeNode(
            doc_id=doc.id,
            concept_name=n,
            summary=s,
            importance=4,
            mastery_score=v,
            status="mastered" if v >= 80 else "learning" if v >= 40 else "weak",
        )
        for n, v, s in LESSON
    ]
    db.add_all(nodes)
    db.flush()
    meanings = {
        "prerequisite": "是理解后者的前置概念",
        "contains": "包含这一具体形式",
        "contrast": "与后者形成对比",
        "applies_to": "可用于分析或调整后者",
    }
    for a, b, r in EDGES:
        db.add(
            KnowledgeEdge(
                doc_id=doc.id,
                source_node_id=nodes[a].id,
                target_node_id=nodes[b].id,
                relation_type=r,
                description=nodes[a].concept_name + meanings[r],
            )
        )
    s = Session(
        user_id=student.id,
        doc_id=doc.id,
        title="用下山解释梯度下降",
        persona_type="peer",
        status="completed",
        ended_at=now(),
    )
    db.add(s)
    db.flush()
    for window, role, msg in [
        ("feynman", "user", "梯度下降就像沿山坡向低处走。"),
        ("feynman", "assistant", "步子过大时，损失一定下降吗？"),
        ("encyclopedia", "user", "学习率过大有什么后果？"),
        ("encyclopedia", "assistant", LESSON[3][2]),
    ]:
        db.add(Message(session_id=s.id, window_type=window, sender_role=role, content=msg))
    issues = ["遗漏步长边界条件", "未区分训练与验证误差", "查证 L1 稀疏性"]
    for idx, kind, issue in zip([3, 4, 6], ["omission", "misconception", "need_lookup"], issues):
        db.add(
            WeaknessLog(session_id=s.id, node_id=nodes[idx].id, weakness_type=kind, detail=issue)
        )
    nodes[6].lookup_count = 1
    db.add(
        DiagnosticReport(
            session_id=s.id,
            coverage_score=88,
            accuracy_score=82,
            clarity_score=91,
            logic_score=79,
            overall_comment="下山类比清晰，仍需解释步长上界及验证误差。",
            weakness_summary=json.dumps(issues, ensure_ascii=False),
            review_plan="比较三种步长的损失曲线；用同一验证集对比 L1 与 L2。",
        )
    )
    rag_store.remove(doc.id)
    rag_store.index(doc.id, title, chunks)
    db.commit()
