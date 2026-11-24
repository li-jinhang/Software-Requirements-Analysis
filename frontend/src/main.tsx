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
  Settings as SettingsIcon,
  GraduationCap,
  ChevronsUpDown,
} from "lucide-react";
import {
  connected,
  createRemote,
  signIn,
  token,
  restoreSpace,
  reportHistory,
  currentService,
  refreshMemory,
  preserveNotes,
  ApiError,
  modelSettingsApi,
  type TeachingPurpose,
} from "./api";
import Library from "./Library";
import { Concept, Course, Space, initial, roles } from "./model";
import { User, student, admin, authenticate, read, write } from "./storage";
import { Dialog } from "./ui";
import { newSession } from "./model";
const Settings = lazy(() => import("./ModelSettings"));
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
          try {
            const nickname = reg ? nick : undefined;
            login(
              await (connected ? signIn : authenticate)(
                name.trim(),
                pass,
                nickname,
              ),
            );
          } catch (e) {
            setErr((e as Error).message);
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
          <button className="primary">{reg ? "注册并登录" : "登录"}</button>
          <button
            type="button"
            onClick={() => {
              setReg(!reg);
              setErr("");
            }}
          >
            {reg ? "已有账号" : "注册账号"}
          </button>
        </div>
      </form>
      <h3>本地演示账号</h3>
      <div className="tools">
        <button onClick={() => login(student)}>学生演示</button>
        <button onClick={() => login(admin)}>管理员演示</button>
      </div>
    </Dialog>
  );
}
function workspaceRoute() {
  return location.hash.slice(1) || "library";
}
function App() {
  const [user, setUser] = useState<User>(
      () => JSON.parse(sessionStorage.getItem("fm.user") || "null") || student,
    ),
    [space, setSpace] = useState(() => read(user.id)),
    [page, setPage] = useState(() =>
      workspaceRoute() === "models" ? "library" : workspaceRoute(),
    ),
    [settings, setSettings] = useState(() => workspaceRoute() === "models"),
    [modelPrompt, setModelPrompt] = useState(""),
    [learningLogin, setLearningLogin] = useState(false),
    [auth, setAuth] = useState(false),
    [nav, setNav] = useState(false),
    [setup, setSetup] = useState(false),
    [doc, setDoc] = useState(""),
    [node, setNode] = useState(""),
    [role, setRole] = useState(roles[1]),
    [sid, setSid] = useState(
      () => sessionStorage.getItem("fm.active:" + user.id) || "",
    ),
    [rid, setRid] = useState(""),
    [err, setErr] = useState(""),
    [starting, setStarting] = useState(false);
  const online = user.id.startsWith("api:");
  const latest = useRef(space);
  const identity = useRef(user.id);
  const [ready, setReady] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!online) return;
    const ctrl = new AbortController();
    const expired = () => {
      login(student);
      setAuth(true);
      setErr("登录已失效，请重新登录。");
    };
    window.addEventListener("fm:expired", expired);
    if (!currentService(user.id) || !token()) {
      expired();
    } else {
      setReady("");
      setErr("");
      restoreSpace(user.id, ctrl.signal)
        .then((remote) => {
          if (ctrl.signal.aborted || identity.current !== user.id) return;
          save((local) => ({
            ...remote,
            courses: remote.courses.map((c) => preserveNotes(c, local.courses.find((d) => d.id === c.id))),
            sessions: remote.sessions.map((s) => ({
              ...s,
              drafts:
                local.sessions.find((v) => v.id === s.id)?.drafts || s.drafts,
            })),
          }));
          setReady(user.id);
        })
        .catch((e) => {
          if (!ctrl.signal.aborted) setErr(e.message);
        });
    }
    return () => {
      ctrl.abort();
      window.removeEventListener("fm:expired", expired);
    };
  }, [user, retry]);
  useEffect(() => {
    if (!online || ready !== user.id || page !== "reports") return;
    const ctrl = new AbortController();
    reportHistory(latest.current, ctrl.signal)
      .then((reports) => {
        if (!ctrl.signal.aborted) save((s) => ({ ...s, reports }));
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) setErr(e.message);
      });
    return () => ctrl.abort();
  }, [page, user.id, ready]);
  const c = space.courses.find((x) => x.id === doc) || space.courses[0],
    sess =
      space.sessions.find((s) => s.id === sid && (online || !s.closed)) ||
      space.sessions.find((s) => !s.closed);
  useEffect(() => {
    if (online) return;
    try {
      write(user.id, latest.current);
    } catch {
      setErr("浏览器空间不足，无法保存工作区。");
    }
  }, [user.id]);
  useEffect(() => {
    const change = () => {
      if (workspaceRoute() === "models") {
        setSettings(true);
        history.replaceState(history.state, "", "#library");
      }
      setPage(workspaceRoute());
      setNav(false);
    };
    if (workspaceRoute() === "models") change();
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  function save(fn: (s: Space) => Space) {
    if (identity.current !== user.id) return;
    const next = fn(latest.current);
    try {
      write(user.id, next);
    } catch {
      setErr("浏览器存储空间不足，请导出并清理部分资料后重试。");
      throw Error("保存失败：本地空间不足。");
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
    identity.current = u.id;
    setErr("");
    setReady("");
    setUser(u);
    latest.current = next;
    setSpace(next);
    setDoc("");
    setSid("");
    setRid("");
    setAuth(false);
    setSettings(false);
    setModelPrompt("");
    setLearningLogin(false);
    setSetup(false);
    go(u.admin ? "admin" : "library");
  }
  useEffect(() => {
    if (sid) sessionStorage.setItem("fm.active:" + user.id, sid);
  }, [sid, user.id]);
  useEffect(() => {
    if (
      !online ||
      ready !== user.id ||
      page !== "graph" ||
      !c ||
      c.parseStatus !== "completed"
    )
      return;
    const ctrl = new AbortController();
    refreshMemory(c, ctrl.signal)
      .then(({ course, logs }) => {
        if (!ctrl.signal.aborted)
          save((s) => ({
            ...s,
            courses: s.courses.map((d) => (d.id === course.id ? course : d)),
            logs: [...s.logs.filter((w) => w.doc !== course.id), ...logs],
          }));
      })
      .catch((e) => {
        if (!ctrl.signal.aborted) setErr(e.message);
      });
    return () => ctrl.abort();
  }, [c?.id, page, ready, user.id]);
  function modelRequired(error: unknown) {
    if (!(error instanceof ApiError) || error.code !== "model_required")
      return false;
    setModelPrompt(error.message);
    return true;
  }
  async function canLearn(purpose: TeachingPurpose) {
    try {
      if (!connected || !(await modelSettingsApi.availability())[purpose]) {
        setModelPrompt("当前操作需要可用的模型，请先配置并分配模型。");
        return false;
      }
    } catch (e) {
      setErr((e as Error).message);
      return false;
    }
    if (!online) {
      setLearningLogin(true);
      return false;
    }
    return true;
  }
  async function begin(course: Course, n?: Concept) {
    if (!course.nodes.length) {
      setErr("资料尚无可讲解的知识点。");
      return;
    }
    if (!(await canLearn("feynman"))) return;
    setDoc(course.id);
    setNode(n?.id || "");
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
        <header className="topline">
          <button
            className="hamb"
            onClick={() => setNav(!nav)}
            aria-label="打开导航"
          >
            <Menu />
          </button>
          <div className="workspace-location">
            <span>个人空间</span>
            <ChevronRight aria-hidden="true" />
            <strong>{title}</strong>
          </div>
          <button
            className="settings-trigger"
            aria-label="设置"
            aria-haspopup="dialog"
            title="设置"
            onClick={() => setSettings(true)}
          >
            <SettingsIcon aria-hidden="true" />
          </button>
        </header>
        <div className={"content " + (page === "studio" ? "studio" : "")}>
          {err && <p role="alert">{err}</p>}
          <Suspense fallback={<p>正在载入…</p>}>
            {online && ready !== user.id ? (
              <div className="box">
                {err ? (
                  <button onClick={() => setRetry((v) => v + 1)}>
                    重新加载
                  </button>
                ) : (
                  "正在恢复服务端学习空间…"
                )}
              </div>
            ) : page === "admin" ? (
              user.admin ? (
                <Admin online={online} />
              ) : (
                <div className="box">
                  <h1>403 · 无权限</h1>
                  <p>后台数据中心仅对管理员开放。</p>
                </div>
              )
            ) : page === "graph" && c ? (
              <Graph
                key={c.id}
                space={space}
                c={c}
                setCourse={setDoc}
                start={begin}
              />
            ) : page === "reports" ? (
              <Reports
                space={space}
                id={rid}
                select={setRid}
                history={
                  online
                    ? (id) => {
                        setSid(id);
                        go("studio");
                      }
                    : undefined
                }
                go={() => go("studio")}
              />
            ) : page === "studio" && sess ? (
              <Workbench
                online={online}
                key={sess.id}
                enterReview={(id, course) => {
                  const review = newSession(id, course, course.nodes[0], sess.role);
                  save(s => ({...s, sessions: [{...review, review: true}, ...s.sessions]}));
                  setSid(id);
                }}
                space={space}
                session={sess}
                save={save}
                canLearn={canLearn}
                modelRequired={modelRequired}
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
                canLearn={canLearn}
                modelRequired={modelRequired}
              />
            )}
          </Suspense>
        </div>
      </main>
      {settings && (
        <Suspense fallback={null}>
          <Settings
            key={user.id}
            online={online}
            close={() => setSettings(false)}
          />
        </Suspense>
      )}
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
              {space.courses
                .filter((d) => d.nodes.length)
                .map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.title}
                  </option>
                ))}
            </select>
          </label>
          <label>
            目标知识点
            <select
                value={node}
              onChange={(e) => setNode(e.target.value)}
            >
                <option value="">按认知文档推荐顺序</option>
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
              if (!online || starting) return;
              setStarting(true);
              setErr("");
              try {
                const id = await createRemote(c.id, role, node);
                const session = newSession(
                  id,
                  c,
                  c.nodes.find((n) => n.id === node) || c.nodes[0],
                  role,
                );
                save((s) => ({ ...s, sessions: [session, ...s.sessions] }));
                setSid(session.id);
                setSetup(false);
                go("studio");
              } catch (e) {
                if (!modelRequired(e)) setErr((e as Error).message);
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
      {modelPrompt && (
        <Dialog title="请配置模型" close={() => setModelPrompt("")}>
          <p>{modelPrompt}</p>
          <button
            className="primary"
            onClick={() => {
              setModelPrompt("");
              setSettings(true);
            }}
          >
            前往模型配置
          </button>
        </Dialog>
      )}
      {learningLogin && (
        <Dialog title="登录学习账号" close={() => setLearningLogin(false)}>
          <p>模型已配置。登录学习账号后即可开始教学并保存学习记录。</p>
          <button
            className="primary"
            onClick={() => {
              setLearningLogin(false);
              setAuth(true);
            }}
          >
            账号登录
          </button>
        </Dialog>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
