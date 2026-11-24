import React, { lazy, Suspense, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  LibraryBig,
  PanelsTopLeft,
  Workflow,
  Files,
  ChartNoAxesColumn,
  Menu,
  BookOpen,
  ChevronRight,
  GraduationCap,
  ChevronsUpDown,
} from "lucide-react";
import { backendUrl, connected, createRemote, currentUser, loadWorkspace, signIn, token } from "./api";
import Library from "./Library";
import { Concept, Course, Space, initial, roles } from "./model";
import { User, student, admin, authenticate, read, write } from "./storage";
import { Dialog } from "./ui";
import { newSession } from "./model";
const Workbench = lazy(() => import("./Workbench"));
import "./style.css";
const Graph = lazy(() =>
    import("./Insights").then((m) => ({ default: m.Graph })),
  ),
  Reports = lazy(() =>
    import("./Insights").then((m) => ({ default: m.Reports })),
  ),
  Admin = lazy(() => import("./Admin"));
const pages = [
  ["library", "学习资料库", LibraryBig],
  ["studio", "双窗工作台", PanelsTopLeft],
  ["graph", "知识图谱", Workflow],
  ["reports", "历史报告", Files],
  ["admin", "后台数据中心", ChartNoAxesColumn],
] as const;
function Auth({
  login,
  close,
}: {
  login: (u: User) => void;
  close: () => void;
}) {
  const [reg, setReg] = useState(false),
    [busy, setBusy] = useState(false),
    [name, setName] = useState(""),
    [pass, setPass] = useState(""),
    [nick, setNick] = useState(""),
    [err, setErr] = useState("");
  return (
    <Dialog
      title={(connected ? "服务端" : "本地") + (reg ? "账号注册" : "账号登录")}
      close={close}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (busy) return;
          setBusy(true);
          setErr("");
          try {
            login(
              await (connected ? signIn : authenticate)(
                name.trim(),
                pass,
                reg ? nick : undefined,
              ),
            );
          } catch (e) {
            setErr((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          用户名
          <input
            required
            autoComplete="username"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        {reg && (
          <label>
            昵称
            <input
              required
              value={nick}
              onChange={(e) => setNick(e.target.value)}
            />
          </label>
        )}
        <label>
          密码
          <input
            type="password"
            required
            minLength={reg ? 6 : 1}
            autoComplete={reg ? "new-password" : "current-password"}
            value={pass}
            onChange={(e) => setPass(e.target.value)}
          />
        </label>
        <p role="alert">{err}</p>
        <div className="tools">
          <button className="primary" disabled={busy}>{busy ? "正在登录…" : reg ? "注册并登录" : "登录"}</button>
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setReg(!reg);
              setErr("");
            }}
          >
            {reg ? "已有账号" : "注册账号"}
          </button>
        </div>
      </form>
      {connected && <p className="meta">服务端演示账号：学生 student / 123456，管理员 admin / admin123。</p>}
      <h3>本地演示账号</h3>
      <div className="tools">
        <button onClick={() => login(student)}>学生演示</button>
        <button onClick={() => login(admin)}>管理员演示</button>
      </div>
    </Dialog>
  );
}
function savedIdentity(): User | null {
  try {
    const saved = JSON.parse(sessionStorage.getItem("fm.user") || "null");
    if (!saved || typeof saved.id !== "string" || typeof saved.name !== "string") return null;
    if (saved.id.startsWith("api:") && (!connected || !saved.id.startsWith(`api:${backendUrl}:`) || !token())) return null;
    return saved;
  } catch { return null; }
}
function App() {
  const [user, setUser] = useState<User>(
      () => savedIdentity() || student,
    ),
    [space, setSpace] = useState(() => read(user.id)),
    [page, setPage] = useState(() => location.hash.slice(1) || "library"),
    [auth, setAuth] = useState(() => connected && !savedIdentity()),
    [nav, setNav] = useState(false),
    [setup, setSetup] = useState(false),
    [doc, setDoc] = useState(""),
    [node, setNode] = useState(""),
    [role, setRole] = useState(roles[1]),
    [sid, setSid] = useState(() => sessionStorage.getItem(`fm.session:${user.id}`) || ""),
    [rid, setRid] = useState(""),
    [err, setErr] = useState(""),
    [starting, setStarting] = useState(false),
    [loading, setLoading] = useState(false),
    [revision, setRevision] = useState(0);
  const online = user.id.startsWith("api:");
  const latest = useRef(space);
  const c = space.courses.find((x) => x.id === doc) || space.courses[0],
    sess =
      space.sessions.find((s) => s.id === sid && !s.closed) ||
      space.sessions.find((s) => !s.closed);
  useEffect(() => {
    if (!online) { setLoading(false); return; }
    const ctrl = new AbortController();
    setLoading(true);
    setErr("");
    Promise.all([currentUser(ctrl.signal), loadWorkspace(read(user.id), ctrl.signal)])
      .then(([verified, loaded]) => {
        if (ctrl.signal.aborted) return;
        if (verified.id !== user.id) throw Error("登录身份已改变，请重新登录。");
        latest.current = loaded;
        setSpace(loaded);
        setUser(verified);
      })
      .catch((e) => { if (!ctrl.signal.aborted) setErr(e.message); })
      .finally(() => { if (!ctrl.signal.aborted) setLoading(false); });
    return () => ctrl.abort();
  }, [user.id, online, revision]);
  useEffect(() => {
    if (sid) sessionStorage.setItem(`fm.session:${user.id}`, sid);
  }, [sid, user.id]);
  useEffect(() => {
    const expired = () => {
      if (!online) return;
      sessionStorage.removeItem("fm.user");
      const local = read(student.id);
      latest.current = local;
      setSpace(local);
      setUser(student);
      setSid("");
      setAuth(true);
      setErr("登录已过期，请重新登录；未发送的草稿已保留。");
    };
    window.addEventListener("fm:auth-expired", expired);
    return () => window.removeEventListener("fm:auth-expired", expired);
  }, [online]);
  useEffect(() => {
    const change = () => {
      setPage(location.hash.slice(1) || "library");
      setNav(false);
    };
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  function save(fn: (s: Space) => Space) {
    const next = fn(latest.current);
    try {
      write(user.id, next);
    } catch {
      setErr(online ? "服务端数据不受影响，但浏览器无法缓存草稿。" : "浏览器存储空间不足，请导出并清理部分资料后重试。");
      if (!online) throw Error("保存失败：本地空间不足。");
    }
    latest.current = next;
    setSpace(next);
  }
  function go(p: string) {
    location.hash = p;
    setPage(p);
    setNav(false);
  }
  function login(u: User) {
    if (!u.id.startsWith("api:")) token("");
    const next = read(u.id);
    sessionStorage.setItem("fm.user", JSON.stringify(u));
    setUser(u);
    latest.current = next;
    setSpace(next);
    setDoc("");
    setSid("");
    setRid("");
    setErr("");
    setAuth(false);
    go(u.admin ? "admin" : "library");
  }
  function begin(course: Course, n?: Concept) {
    setDoc(course.id);
    setNode(n?.id || course.nodes[0].id);
    setSetup(true);
  }
  const title = pages.find((p) => p[0] === page)?.[1] || "学习资料库";
  const links = pages.filter((p) => p[0] !== "admin" || user.admin);
  return (
    <div className="shell">
      {nav && (
        <button
          className="scrim"
          aria-label="关闭导航"
          onClick={() => setNav(false)}
        />
      )}
      <aside className={"sidebar " + (nav ? "open" : "")}>
        <div className="brand">
          <div className="brandicon">
            <BookOpen />
          </div>
          <div>
            <strong>智溯费曼</strong>
            <small>FeynmanMind</small>
          </div>
        </div>
        <div className="navcaption">学习空间</div>
        <nav>
          {links.map(([key, label, Icon]) => (
            <button
              key={key}
              aria-current={page === key ? "page" : undefined}
              onClick={() => go(key)}
            >
              <Icon />
              {label}
              {page === key && <ChevronRight className="end" />}
            </button>
          ))}
        </nav>
        <div className="account">
          <button
            onClick={() => {
              setNav(false);
              setAuth(true);
            }}
          >
            <GraduationCap />
            <span>
              {user.name}
              <small>
                {user.admin ? "管理员" : "学习者"} ·{" "}
                {online ? "在线服务" : "本地演示"}
              </small>
            </span>
            <ChevronsUpDown />
          </button>
        </div>
      </aside>
      <main>
        <div className="topline">
          <button
            className="hamb"
            onClick={() => setNav(!nav)}
            aria-label="打开导航"
          >
            <Menu />
          </button>
          <span>个人空间</span>
          <ChevronRight />
          {title}
        </div>
        <div className={"content " + (page === "studio" ? "studio" : "")}>
          {err && <p role="alert">{err} {online && <button onClick={() => setRevision((v) => v + 1)}>重新同步</button>}</p>}
          {loading ? <p role="status" aria-live="polite">正在同步服务端学习资料和历史记录…</p> :
          <Suspense fallback={<p>正在载入…</p>}>
            {page === "admin" ? (
              user.admin ? (
                <Admin online={online} />
              ) : (
                <div className="box">
                  <h1>403 · 无权限</h1>
                  <p>后台数据中心仅对管理员开放。</p>
                </div>
              )
            ) : page === "graph" && c ? (
              <Graph space={space} c={c} setCourse={setDoc} start={begin} />
            ) : page === "reports" ? (
              <Reports
                space={space}
                id={rid}
                select={setRid}
                go={() => go("studio")}
              />
            ) : page === "studio" && sess ? (
              <Workbench
                online={online}
                key={sess.id}
                space={space}
                session={sess}
                save={save}
                choose={() =>
                  begin(space.courses.find((d) => d.id === sess.doc) || c)
                }
                graph={() => {
                  setDoc(sess.doc);
                  go("graph");
                }}
                report={(id) => {
                  setRid(id);
                  go("reports");
                }}
              />
            ) : ["studio", "graph"].includes(page) ? (
              <div className="box empty">
                <PanelsTopLeft />
                <h1>{title}</h1>
                <p>
                  {c
                    ? "选择课程和听众，开始一轮费曼教学。"
                    : "上传资料或载入示范课程，即可开始学习。"}
                </p>
                <button
                  className="primary"
                  onClick={() => {
                    if (c) begin(c);
                    else if (online) go("library");
                    else {
                      const sample = initial().courses;
                      save((s) => ({ ...s, courses: sample }));
                      begin(sample[0]);
                    }
                  }}
                >
                  {c ? "开始教学" : online ? "前往资料库" : "载入示范资料"}
                </button>
              </div>
            ) : (
              <Library
                online={online}
                space={space}
                save={save}
                start={begin}
              />
            )}
          </Suspense>}
        </div>
      </main>
      {auth && <Auth login={login} close={() => setAuth(false)} />}
      {setup && c && (
        <Dialog
          title="开始费曼教学"
          close={() => {
            if (!starting) setSetup(false);
          }}
        >
          {space.sessions.some((s) => !s.closed) && (
            <label>
              继续已有会话
              <select
                value=""
                onChange={(e) => {
                  setSid(e.target.value);
                  setSetup(false);
                  go("studio");
                }}
              >
                <option value="" disabled>
                  选择进行中的会话
                </option>
                {space.sessions
                  .filter((s) => !s.closed)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {space.courses.find((c) => c.id === s.doc)?.title} ·{" "}
                      {s.role} · {s.left.filter((m) => m.who === "我").length}{" "}
                      轮
                    </option>
                  ))}
              </select>
            </label>
          )}
          <label>
            学习资料
            <select
              value={c.id}
              onChange={(e) => {
                setDoc(e.target.value);
                setNode("");
              }}
            >
              {space.courses.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            目标知识点
            <select
              value={node || c.nodes[0].id}
              onChange={(e) => setNode(e.target.value)}
            >
              {c.nodes.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            听众角色
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {roles.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>
          {err && <p role="alert">{err}</p>}
          <button
            className="primary"
            disabled={starting}
            onClick={async () => {
              setStarting(true);
              setErr("");
              try {
                const session = newSession(
                  c,
                  c.nodes.find((n) => n.id === node) || c.nodes[0],
                  role,
                );
                if (online) {
                  session.id = await createRemote(c.id, session.focus, role);
                  session.left = [];
                }
                save((s) => ({ ...s, sessions: [session, ...s.sessions] }));
                setSid(session.id);
                setSetup(false);
                go("studio");
              } catch (e) {
                setErr((e as Error).message);
              } finally {
                setStarting(false);
              }
            }}
          >
            {starting ? "正在创建…" : "开始教学"}
            <ChevronRight />
          </button>
        </Dialog>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
