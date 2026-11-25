import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import bcrypt
from sqlalchemy import select
from app.core.config import ROOT, settings
from app.core.security import hash_password
from app.db.models import (
    User,
    RetiredAccountId,
    DemoWorkspace,
    Document,
    CognitiveItem,
    KnowledgeNode,
    KnowledgeEdge,
    Session,
    Message,
    WeaknessLog,
    DiagnosticReport,
    next_account_id,
)
from app.services import cognitive, extension_store, memory_service, rag_store
from app.services.course_library import CurriculumPlan
from app.services.doc_parser import split_chunks

DEMO_TITLE = "机器学习基础：梯度下降与过拟合"
NETWORK_TITLE = "计算机网络"
NETWORK_CURRICULUM = Path(__file__).with_name("demo_network_curriculum.json")
DEMO_DATE = datetime(2024, 1, 10, 9)
DEMO_NICKNAME = "演示学生"

LESSON = [
    (
        "损失函数",
        ("mastered", "partial"),
        "损失函数衡量预测值与真实标签的偏差。训练时希望减小训练损失，而评估泛化能力需要独立的验证数据。",
    ),
    (
        "梯度",
        ("mastered", "mastered"),
        "梯度由损失对各参数的偏导数组成，给出局部上升最快的方向；沿负梯度走一个足够小的步长，通常会降低当前损失。",
    ),
    (
        "梯度下降",
        ("mastered", "mastered"),
        r"梯度下降按 $w_{t+1}=w_t-\eta\nabla L(w_t)$ 更新参数。把损失曲面想成山坡，梯度决定下坡方向，学习率控制每一步走多远。",
    ),
    (
        "学习率",
        ("wrong", "partial", "untested"),
        "学习率过小会使迭代缓慢，过大会在谷底两侧震荡甚至发散。可观察训练损失曲线并比较多个步长；衰减策略在训练后期减小步长。",
    ),
    (
        "过拟合",
        ("wrong", "partial"),
        "当训练误差持续下降而验证误差上升，模型可能正在记忆训练样本中的噪声。这是过拟合的典型信号，应比较训练集和验证集表现。",
    ),
    (
        "正则化",
        ("mastered", "partial"),
        "正则化向目标函数加入复杂度惩罚，以限制模型过度拟合。系数过大也会妨碍模型学习有效信号，因此需用验证集选择强度。",
    ),
    (
        "L1 正则化",
        ("untested",),
        "L1 惩罚取参数绝对值之和，在适当条件下会让部分系数恰好为零，适合需要稀疏权重的场景。",
    ),
    (
        "L2 正则化",
        ("mastered", "partial"),
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
NETWORK_GRADES = {
    "模块一：网络基础": ("mastered", "mastered"),
    "分层模型与封装": ("mastered", "mastered"),
    "物理层与比特传输": ("mastered", "mastered"),
    "时延与吞吐量": ("mastered", "partial"),
    "模块二：数据链路": ("mastered", "partial"),
    "成帧与差错检测": ("mastered", "mastered"),
    "以太网与交换": ("mastered", "mastered"),
    "模块三：网络层": ("partial", "wrong"),
    "IPv4、IPv6 与 CIDR 子网": ("partial", "wrong"),
    "ARP 与下一跳地址解析": ("partial", "untested"),
    "路由、转发与 NAT": ("mastered", "untested"),
    "ICMP 与连通性诊断": ("mastered", "partial"),
    "模块四：传输层": ("partial", "wrong"),
    "端口与 UDP": ("mastered", "mastered"),
    "TCP 连接、可靠性与控制": ("mastered", "wrong"),
}
NETWORK_NOTES = {
    "时延与吞吐量": "能算发送时延。下次练习比较两条不同速率的链路，找出端到端的吞吐量瓶颈。",
    "IPv4、IPv6 与 CIDR 子网": "能用掩码找网络地址，还要练习可用地址范围，并对照 /30、/31、/32。",
    "ARP 与下一跳地址解析": "画出本机、网关和远端主机。跨网段时，要先确定本地链路上解析的是谁的地址。",
    "TCP 连接、可靠性与控制": "能解释序列号与重传，仍需区分接收窗口和拥塞窗口分别受什么限制。",
}


def build_fingerprint():
    if settings.demo_build_id:
        progress = json.dumps([NETWORK_GRADES, NETWORK_NOTES], ensure_ascii=False, sort_keys=True).encode()
        return sha256(b"\0".join([
            settings.demo_build_id.encode(), NETWORK_CURRICULUM.read_bytes(), progress
        ])).hexdigest()
    frontend = ROOT.parent / "frontend"
    paths = [
        *ROOT.joinpath("app").rglob("*.py"),
        NETWORK_CURRICULUM,
        *(p for p in frontend.joinpath("src").rglob("*") if p.is_file()),
        ROOT / "requirements.txt",
        *(frontend / name for name in ("index.html", "package-lock.json", "vite.config.ts", "dist/build-id.txt")),
    ]
    fingerprint = sha256()
    for path in sorted(p for p in paths if p.is_file()):
        fingerprint.update(path.relative_to(ROOT.parent).as_posix().encode())
        fingerprint.update(b"\0")
        fingerprint.update(sha256(path.read_bytes()).digest())
    return fingerprint.hexdigest()


def is_legacy_demo(student):
    if student.role != "student" or student.nickname != DEMO_NICKNAME:
        return False
    try:
        return bcrypt.checkpw(b"123456", student.password_hash.encode())
    except ValueError:
        return False


def seed(db):
    build_id = build_fingerprint()
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    record = db.get(DemoWorkspace, 1)
    if record and record.build_id == build_id:
        repair_demo_index(db, record.user_id)
        db.commit()
        return
    has_users = db.scalar(select(User.id).limit(1)) is not None
    if not record and not has_users and db.scalar(select(RetiredAccountId.id).limit(1)) is not None:
        db.commit()
        return
    student = db.get(User, record.user_id) if record and record.user_id else None
    if student is None:
        student = db.scalar(select(User).where(User.username == "student"))
        if student is not None and not is_legacy_demo(student):
            db.commit()
            return
    if not record and not has_users:
        db.add(User(id=1, username="admin", nickname="教学管理员", role="admin",
                    password_hash=hash_password("admin123")))
        db.flush()
    if student is None:
        student = User(id=next_account_id(db), username="student", nickname=DEMO_NICKNAME,
                       password_hash=hash_password("123456"), created_at=DEMO_DATE)
        db.add(student)
        db.flush()
    student.nickname = DEMO_NICKNAME
    old_docs = db.scalars(select(Document).where(Document.user_id == student.id)).all()
    old_sessions = db.scalars(select(Session.id).where(Session.user_id == student.id)).all()
    network_plan = CurriculumPlan.model_validate_json(NETWORK_CURRICULUM.read_bytes())
    sources = [
        "# " + DEMO_TITLE + "\n\n" + "\n\n".join("## " + n + "\n" + s for n, _, s in LESSON),
        cognitive.plan_source(NETWORK_TITLE, network_plan),
    ]
    paths = [settings.data_dir / "uploads" / f"demo-course-{uuid4().hex}.md" for _ in sources]
    chunks = [split_chunks(source) for source in sources]
    indexed_ids = []
    try:
        machine_learning = seed_course(db, student.id, paths[0], len(chunks[0]))
        network = Document(
            user_id=student.id, title=NETWORK_TITLE, subject=NETWORK_TITLE, is_library=True,
            file_type="md", file_path=str(paths[1]), parse_status="completed", chunk_count=len(chunks[1]),
            structured_summary=network_plan.structured_summary, created_at=DEMO_DATE,
        )
        db.add(network)
        db.flush()
        cognitive.store_plan(db, network.id, network_plan)
        seed_progress(db, network.id, NETWORK_GRADES)
        for node in db.scalars(select(KnowledgeNode).where(
            KnowledgeNode.doc_id == network.id, KnowledgeNode.concept_name.in_(NETWORK_NOTES),
        )):
            node.user_note = NETWORK_NOTES[node.concept_name]
        db.merge(DemoWorkspace(id=1, user_id=student.id, build_id=build_id))
        for doc, path, source, blocks in zip((machine_learning, network), paths, sources, chunks):
            path.write_text(source, encoding="utf-8")
            indexed_ids.append(doc.id)
            rag_store.remove(doc.id)
            rag_store.index(doc.id, doc.title, blocks)
        # Commit the replacement and revision together. Old sources use the existing deletion journal.
        memory_service.delete_documents(db, old_docs)
    except Exception:
        try:
            for doc_id in indexed_ids:
                rag_store.remove(doc_id)
        finally:
            db.rollback()
            for path in paths:
                path.unlink(missing_ok=True)
        raise
    extension_store.purge_sessions(student.id, old_sessions)


def repair_demo_index(db, student_id):
    if student_id is None:
        return
    courses = db.scalars(select(Document).where(
        Document.user_id == student_id, Document.title.in_((DEMO_TITLE, NETWORK_TITLE)),
        Document.parse_status == "completed", Document.file_path.contains("demo-course-"),
    ))
    for demo in courses:
        path = Path(demo.file_path)
        if not path.is_file():
            continue
        chunks = split_chunks(path.read_text(encoding="utf-8"))
        if chunks and demo.chunk_count != len(chunks):
            rag_store.remove(demo.id)
            rag_store.index(demo.id, demo.title, chunks)
            demo.chunk_count = len(chunks)


def seed_course(db, student_id, path, chunk_count):
    doc = Document(
        user_id=student_id,
        title=DEMO_TITLE,
        subject="机器学习",
        is_library=True,
        file_path=str(path),
        file_type="md",
        parse_status="completed",
        chunk_count=chunk_count,
        structured_summary="从损失最小化到泛化误差与正则化。",
        created_at=DEMO_DATE,
    )
    db.add(doc)
    db.flush()
    nodes = [
        KnowledgeNode(
            doc_id=doc.id,
            concept_name=n,
            summary=s,
            importance=4,
        )
        for n, _, s in LESSON
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
        user_id=student_id,
        doc_id=doc.id,
        title="用下山解释梯度下降",
        persona_type="peer",
        status="completed",
        created_at=DEMO_DATE,
        ended_at=DEMO_DATE,
    )
    db.add(s)
    db.flush()
    for window, role, msg in [
        ("feynman", "user", "梯度下降就像沿山坡向低处走。"),
        ("feynman", "assistant", "步子过大时，损失一定下降吗？"),
        ("encyclopedia", "user", "学习率过大有什么后果？"),
        ("encyclopedia", "assistant", LESSON[3][2]),
    ]:
        db.add(Message(session_id=s.id, window_type=window, sender_role=role, content=msg, created_at=DEMO_DATE))
    issues = ["遗漏步长边界条件", "未区分训练与验证误差", "查证 L1 稀疏性"]
    for idx, kind, issue in zip([3, 4, 6], ["omission", "misconception", "need_lookup"], issues):
        db.add(
            WeaknessLog(session_id=s.id, node_id=nodes[idx].id, weakness_type=kind, detail=issue,
                        created_at=DEMO_DATE)
        )
    nodes[6].lookup_count = 1
    nodes[3].user_note = "用同一个损失函数比较三种步长，观察收敛速度和是否震荡。"
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
            created_at=DEMO_DATE,
        )
    )
    cognitive.from_nodes(db, doc.id)
    seed_progress(db, doc.id, {name: values for name, values, _ in LESSON})
    return doc


def seed_progress(db, doc_id, grades):
    remaining = {name: iter(values) for name, values in grades.items()}
    marks = []
    for item in db.scalars(select(CognitiveItem).where(CognitiveItem.doc_id == doc_id)
                           .order_by(CognitiveItem.item_order)):
        if item.section_title not in remaining:
            continue
        grade = next(remaining[item.section_title])
        if grade == "untested":
            continue
        if grade == "mastered":
            item.next_review_at = DEMO_DATE
        marks.append({
            "item_id": item.id,
            "status": grade,
            "user_evidence": item.statement if grade == "mastered" else "",
            "issues": [],
        })
    cognitive.apply_marks(db, doc_id, marks)
