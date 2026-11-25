import React, { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  LibraryBig,
  PanelsTopLeft,
  Workflow,
  Files,
  ChartNoAxesColumn,
  Moon,
  Sun,
  BookOpen,
  Backpack,
  UsersRound,
  ClipboardCheck,
  ChevronRight,
  Settings as SettingsIcon,
  GraduationCap,
  ChevronsUpDown,
  RotateCcw,
  Plus,
  FileText,
  MessagesSquare,
} from "lucide-react";
import {
  connected,
  createRemote,
  createGuidedSession,
  signIn,
  token,
  restoreSpace,
  reportHistory,
  currentService,
  localModelSettings,
  refreshMemory,
  preserveNotes,
  reviews,
  ApiError,
  modelSettingsApi,
  type TeachingPurpose,
} from "./api";
import Library from "./Library";
import LocalAccounts from "./LocalAccounts";
const CapabilityCenter = lazy(() => import("./CapabilityCenter"));
import { Concept, Course, Space, initial, mean, roles } from "./model";
import { User, student, admin, authenticate, clearWorkspaceCache, read, write } from "./storage";
import { Dialog, WorkspaceFrame, type ColorTheme } from "./ui";
import { newSession } from "./model";
const Settings = lazy(() => import("./ModelSettings"));
const Workbench = lazy(() => import("./Workbench"));
const GuidedChat = lazy(() => import("./GuidedChat"));
const Review = lazy(() => import("./Review"));
import "./style.css";
import "./guided-chat.css";
const Graph = lazy(() =>
    import("./Insights").then((m) => ({ default: m.Graph })),
  ),
  Reports = lazy(() =>
    import("./Insights").then((m) => ({ default: m.Reports })),
  ),
  Admin = lazy(() => import("./Admin"));
const pages = [
  ["library", "学习资料库", LibraryBig],
  ["tutor", "AI 讲解", GraduationCap],
  ["studio", "双窗工作台", PanelsTopLeft],
  ["graph", "知识图谱", Workflow],
  ["review", "复习", RotateCcw],
  ["reports", "历史报告", Files],
  ["admin", "后台数据中心", ChartNoAxesColumn],
] as const;

class GraphLoadBoundary extends React.Component<React.PropsWithChildren> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="box empty">
        <h1>知识记忆图谱</h1>
        <p role="alert">图谱加载失败，请重新加载工作台。</p>
        <button className="primary" onClick={() => window.location.reload()}>
          重新加载工作台
        </button>
      </div>
    );
  }
}

function Auth({
  login,
  close,
  logout,
}: {
  login: (u: User) => void;
  close: () => void;
  logout?: () => void;
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
      {!connected && (
        <>
          <h3>本地演示账号</h3>
          <div className="tools">
            <button onClick={() => login(student)}>学生演示</button>
            <button onClick={() => login(admin)}>管理员演示</button>
          </div>
        </>
      )}
      {logout && <button onClick={logout}>退出当前账号</button>}
    </Dialog>
  );
}
function workspaceRoute() {
  return location.hash.slice(1) || "library";
}
const themeKey = "fm.theme";
function storedTheme(): ColorTheme {
  try { return localStorage.getItem(themeKey) === "dark" ? "dark" : "light"; }
  catch { return "light"; }
}


const userKey = connected ? "fm.user" : "fm.browserUser";

function storedUser(): User | null {
  try {
    const user = JSON.parse(sessionStorage.getItem(userKey) || "null") as User | null;
    if (!user || typeof user.id !== "string" || typeof user.name !== "string" || typeof user.admin !== "boolean") return null;
    if (connected ? !currentService(user.id) || !token() : user.id.startsWith("api:")) return null;
    return user;
  } catch {
    return null;
  }
}

type Appearance = { theme: ColorTheme; toggleTheme: () => void };

function ThemeToggle({ theme, toggleTheme }: Appearance) {
  const title = theme === "light" ? "切换到深色主题" : "切换到浅色主题";
  return (
    <button type="button" className="icon-button theme-toggle" aria-label="深色主题"
      aria-pressed={theme === "dark"} title={title} onClick={toggleTheme}>
      {theme === "light" ? <Moon aria-hidden="true" /> : <Sun aria-hidden="true" />}
    </button>
  );
}

function AccountEntry({ children, theme, toggleTheme }: React.PropsWithChildren<Appearance>) {
  return (
    <main className="account-entry">
      <ThemeToggle theme={theme} toggleTheme={toggleTheme} />
      <section className="entry-panel" aria-labelledby="entry-title">
        <header className="entry-brand">
          <BookOpen aria-hidden="true" />
          <h1 id="entry-title">智溯费曼</h1>
        </header>
        {children}
      </section>
    </main>
  );
}

function AccountDialog({ current, login, close, logout, onDeleted }: {
  current?: User;
  login: (user: User) => void;
  close: () => void;
  logout?: () => void;
  onDeleted?: () => void;
}) {
  return localModelSettings
    ? <LocalAccounts current={current} login={login} close={close} logout={logout} onDeleted={onDeleted} />
    : <Auth login={login} close={close} logout={logout} />;
}

function App() {
  const [theme, setTheme] = useState<ColorTheme>(storedTheme);
  useLayoutEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === themeKey || event.key === null) setTheme(storedTheme());
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);
  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    try { localStorage.setItem(themeKey, next); }
    catch { /* The current window can still switch appearance when storage is unavailable. */ }
  }

  const [user, setUser] = useState(storedUser);
  const [accountOpen, setAccountOpen] = useState(false);
  const [message, setMessage] = useState("");

  function login(next: User) {
    const route = workspaceRoute();
    const page = next.admin ? "admin" : !user && route !== "admin" &&
      (route === "models" || pages.some(([key]) => key === route)) ? route : "library";
    history.replaceState(history.state, "", "#" + page);
    sessionStorage.setItem(userKey, JSON.stringify(next));
    setMessage("");
    setAccountOpen(false);
    setUser(next);
  }

  function logout(reason = "") {
    token("");
    sessionStorage.removeItem(userKey);
    setMessage(reason);
    setAccountOpen(false);
    setUser(null);
  }

  if (user) {
    return <Workspace key={user.id} user={user} login={login} logout={logout} theme={theme} toggleTheme={toggleTheme} />;
  }

  return (
    <AccountEntry theme={theme} toggleTheme={toggleTheme}>
      <p>登录后打开你的学习空间。</p>
      {message && <p role="alert">{message}</p>}
      <button className="primary" onClick={() => setAccountOpen(true)}>
        {localModelSettings ? "选择本机账号" : "登录账号"}
      </button>
      {accountOpen && <AccountDialog login={login} close={() => setAccountOpen(false)} />}
    </AccountEntry>
  );
}

function Workspace({ user, login, logout, theme, toggleTheme }: Appearance & {
  user: User;
  login: (user: User) => void;
  logout: (reason?: string) => void;
}) {
  const [space, setSpace] = useState(() => read(user.id)),
    [page, setPage] = useState(() =>
      workspaceRoute() === "models" ? "library" : workspaceRoute(),
    ),
    [settings, setSettings] = useState(() => workspaceRoute() === "models"),
    [capabilities, setCapabilities] = useState<false | "skills" | "mcp">(false),
    [modelPrompt, setModelPrompt] = useState(""),
    [auth, setAuth] = useState(false),
    [nav, setNav] = useState(false),
    [setup, setSetup] = useState(false),
    [doc, setDoc] = useState(""),
    [node, setNode] = useState(""),
    [role, setRole] = useState(roles[1]),
    [sid, setSid] = useState(
      () => sessionStorage.getItem("fm.active:" + user.id) || "",
    ),
    [guideId, setGuideId] = useState(
      () => sessionStorage.getItem("fm.guided:" + user.id) || "",
    ),
    [rid, setRid] = useState(""),
    [err, setErr] = useState(""),
    [starting, setStarting] = useState(false),
    [uploadTick, setUploadTick] = useState(0),
    [dueCount, setDueCount] = useState(0),
    [modelStatus, setModelStatus] = useState<{ ready: boolean; label: string } | null>(null);
  const online = user.id.startsWith("api:");
  const latest = useRef(space);
  const identity = useRef(user.id);
  useEffect(() => () => { identity.current = ""; }, []);
  const [ready, setReady] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    if (!online) return;
    const ctrl = new AbortController();
    const expired = () => {
      logout("登录已失效，请重新选择账号。");
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
  const c = space.courses.find((x) => x.id === doc) || space.courses[0];
  const practiceSessions = space.sessions.filter((s) => !s.guided);
  const guideSessions = space.sessions.filter((s) => s.guided);
  const sess = practiceSessions.find((s) => s.id === sid && (online || !s.closed)) ||
    practiceSessions.find((s) => !s.closed);
  const oldGuide = online && ready === user.id && guideSessions.some((s) => s.id === sid);
  const legacyStudioRoute = page === "studio" && oldGuide;
  const guideSession = guideSessions.find((s) => s.id === (legacyStudioRoute ? sid : guideId)) || guideSessions[0];
  const guideCourse = space.courses.find((course) => course.id === guideSession?.doc);
  const currentPage = legacyStudioRoute ? "tutor" : page;
  useEffect(() => {
    if (!oldGuide) return;
    if (legacyStudioRoute || !guideId) setGuideId(sid);
    setSid("");
    if (legacyStudioRoute) {
      history.replaceState(history.state, "", "#tutor");
      setPage("tutor");
    }
  }, [oldGuide, legacyStudioRoute, guideId, sid]);
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
  function openReport(id: string) {
    setRid(id);
    go("reports");
  }
  useEffect(() => {
    const key = "fm.active:" + user.id;
    if (sid) sessionStorage.setItem(key, sid);
    else sessionStorage.removeItem(key);
  }, [sid, user.id]);
  useEffect(() => {
    if (guideId) sessionStorage.setItem("fm.guided:" + user.id, guideId);
  }, [guideId, user.id]);
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
  useEffect(() => {
    if (!online || ready !== user.id || !c || (c.parseStatus && c.parseStatus !== "completed")) {
      setDueCount(0);
      return;
    }
    const ctrl = new AbortController();
    reviews.get(c.id, ctrl.signal)
      .then((data) => {
        if (!ctrl.signal.aborted) setDueCount(data.due_items.length);
      })
      .catch(() => {
        if (!ctrl.signal.aborted) setDueCount(0);
      });
    return () => ctrl.abort();
  }, [c?.id, page, ready, user.id, online]);
  useEffect(() => {
    if (!connected || !online || ready !== user.id || settings) return;
    let alive = true;
    const loadModel = async () => {
      try {
        if (localModelSettings) {
          const s = await modelSettingsApi.read();
          if (!alive) return;
          const def = s.profiles.find((p) => p.id === s.bindings.default);
          if (def) setModelStatus({ ready: true, label: def.model || def.name });
          else if (s.environment_live) setModelStatus({ ready: true, label: "环境默认模型" });
          else setModelStatus({ ready: false, label: "去配置模型" });
        } else {
          const active = await modelSettingsApi.active();
          if (!alive) return;
          const first = active.feynman || active.encyclopedia || active.extract || active.report;
          setModelStatus(first ? { ready: true, label: first } : { ready: false, label: "去配置模型" });
        }
      } catch {
        if (alive) setModelStatus(null);
      }
    };
    void loadModel();
    return () => { alive = false; };
  }, [online, ready, user.id, settings]);
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
    return online;
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
  async function beginGuided(course: Course, concept?: Concept) {
    if (starting) return;
    setStarting(true);
    setErr("");
    try {
      if (!(await canLearn("encyclopedia")) || identity.current !== user.id) return;
      const { id, target } = await createGuidedSession(course.id, role, concept?.id);
      if (identity.current !== user.id) return;
      const session = { ...newSession(id, course, course.nodes[0], role), focus: target, guided: true };
      save((s) => ({ ...s, sessions: [session, ...s.sessions] }));
      setDoc(course.id);
      setGuideId(id);
      go("tutor");
    } catch (e) {
      if (identity.current === user.id && !modelRequired(e)) setErr((e as Error).message);
    } finally {
      setStarting(false);
    }
  }
  const pageInfo = pages.find((p) => p[0] === currentPage) || pages[0];
  const [, title, PageIcon] = pageInfo;
  const isWorkspace = ["studio", "tutor", "graph", "review", "reports"].includes(currentPage);
  const links = pages.filter((p) => p[0] !== "admin" || user.admin);
  const recentSessions = space.sessions.slice(0, 4);
  function selectCourseFromRail(course: Course) {
    setDoc(course.id);
    if (currentPage === "tutor") {
      const existingGuide = guideSessions.find((s) => s.doc === course.id);
      if (existingGuide) { setGuideId(existingGuide.id); go("tutor"); return; }
    } else if (currentPage === "studio") {
      const existingStudio = practiceSessions.find((s) => s.doc === course.id && !s.closed);
      if (existingStudio) { setSid(existingStudio.id); go("studio"); return; }
    } else if (currentPage === "review") {
      go("review");
      return;
    }
    go("graph");
  }
  function openSessionFromRail(s: Space["sessions"][number]) {
    setDoc(s.doc);
    if (s.guided) {
      setGuideId(s.id);
      go("tutor");
    } else {
      setSid(s.id);
      go("studio");
    }
  }
  if (online && ready !== user.id) {
    return (
      <AccountEntry theme={theme} toggleTheme={toggleTheme}>
        {err ? <p role="alert">{err}</p> : <p role="status">正在读取当前账号的学习数据…</p>}
        {err && <button className="primary" onClick={() => setRetry((value) => value + 1)}>重新加载</button>}
        <button onClick={() => logout()}>返回账号选择</button>
      </AccountEntry>
    );
  }
  return (
    <div className="shell">
      {nav && (
        <button
          className="scrim"
          aria-label="关闭导航"
          onClick={() => setNav(false)}
        />
      )}
      <aside id="learning-navigation" className={"sidebar " + (nav ? "open" : "")}>
        <div className="brand">
          <BookOpen aria-hidden="true" />
          <strong>智溯费曼</strong>
        </div>
        <div className="sidebar-scroll">
          <div className="navcaption">学习空间</div>
          <nav aria-label="学习导航">
            {links.map(([key, label, Icon]) => (
              <button
                key={key}
                aria-label={label}
                aria-current={currentPage === key ? "page" : undefined}
                onClick={() => go(key)}
              >
                <Icon />
                <span className="truncate">{label}</span>
                {key === "review" && dueCount > 0 && (
                  <span className="nav-badge" aria-hidden="true">{dueCount}</span>
                )}
              </button>
            ))}
          </nav>

          <div className="navcaption navcaption-row">
            <span>我的课程</span>
            <button
              type="button"
              className="icon-button nav-add"
              aria-label="添加学习资料"
              title="上传或新建课程"
              onClick={() => {
                go("library");
                setUploadTick((t) => t + 1);
              }}
            >
              <Plus aria-hidden="true" />
            </button>
          </div>
          <nav aria-label="我的课程">
            {space.courses.map((course) => {
              const avg = mean(course.nodes.map((n) => n.score));
              const active = c?.id === course.id;
              return (
                <button
                  key={course.id}
                  type="button"
                  aria-current={active ? "true" : undefined}
                  title={course.title}
                  onClick={() => selectCourseFromRail(course)}
                >
                  <FileText aria-hidden="true" />
                  <span className="truncate">{course.title}</span>
                  <span className="nav-meta">{avg}%</span>
                </button>
              );
            })}
            {!space.courses.length && <p className="nav-empty">暂无课程</p>}
          </nav>

          {recentSessions.length > 0 && (
            <>
              <div className="navcaption">最近学习</div>
              <nav aria-label="最近学习">
                {recentSessions.map((s) => {
                  const course = space.courses.find((d) => d.id === s.doc);
                  const node = course?.nodes.find((n) => n.id === s.focus);
                  const sessionTitle = s.guided
                    ? node
                      ? `${node.name} · ${course?.title || "讲解"}`
                      : course?.title || "AI 讲解"
                    : `${course?.title || "费曼练习"} · ${s.role}`;
                  const active = s.guided
                    ? currentPage === "tutor" && guideSession?.id === s.id
                    : currentPage === "studio" && sess?.id === s.id;
                  return (
                    <button
                      key={s.id}
                      type="button"
                      aria-current={active ? "true" : undefined}
                      title={sessionTitle}
                      onClick={() => openSessionFromRail(s)}
                    >
                      {s.guided ? <GraduationCap aria-hidden="true" /> : <MessagesSquare aria-hidden="true" />}
                      <span className="truncate">{sessionTitle}</span>
                    </button>
                  );
                })}
              </nav>
            </>
          )}
        </div>
        {online && modelStatus && (
          <button
            type="button"
            className="sidebar-model"
            title={modelStatus.ready ? `当前默认模型：${modelStatus.label}` : "点击前往配置模型"}
            onClick={() => { setNav(false); setSettings(true); }}
          >
            <i className={"model-dot " + (modelStatus.ready ? "ready" : "missing")} aria-hidden="true" />
            <span className="truncate">{modelStatus.label}</span>
          </button>
        )}
        <footer className="sidebar-footer">
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
                  {online
                    ? localModelSettings
                      ? "本机账号"
                      : "在线服务"
                    : "浏览示范"}
                </small>
              </span>
              <ChevronsUpDown />
            </button>
          </div>
          <ThemeToggle theme={theme} toggleTheme={toggleTheme} />
          <button className="icon-button settings-trigger" aria-label="设置" aria-haspopup="dialog"
            title="设置" onClick={() => { setNav(false); setSettings(true); }}>
            <SettingsIcon aria-hidden="true" />
          </button>
        </footer>
      </aside>
      <WorkspaceFrame
        title={currentPage === "graph" ? "知识记忆图谱" : title}
        icon={PageIcon}
        heading={isWorkspace}
        navOpen={nav}
        onMenu={() => setNav(!nav)}
        onCapabilities={() => online ? setCapabilities("skills") : setAuth(true)}
      >
        <div className={"content view-" + pageInfo[0]}>
          {err && <p role="alert">{err}</p>}
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
              <GraphLoadBoundary key={c.id}>
                <Graph theme={theme} space={space} c={c} setCourse={setDoc} start={begin}
                  guide={beginGuided} starting={starting} />
              </GraphLoadBoundary>
            ) : page === "reports" ? (
              <Reports
                theme={theme}
                space={space}
                id={rid}
                select={setRid}
                practice={begin}
                guide={beginGuided}
                history={
                  online
                    ? (id) => {
                        if (guideSessions.some((session) => session.id === id)) {
                          setGuideId(id);
                          go("tutor");
                        } else {
                          setSid(id);
                          go("studio");
                        }
                      }
                    : undefined
                }
                go={() => go("studio")}
              />
            ) : page === "review" ? (
              <Review courses={space.courses} course={c} online={online} select={setDoc} assess={begin}
                account={() => setAuth(true)} library={() => go("library")} />
            ) : currentPage === "tutor" ? (
              guideSession && guideCourse ? (
                <GuidedChat key={guideSession.id} session={guideSession} course={guideCourse}
                  historyOptions={guideSessions.map((session) => {
                    const course = space.courses.find((doc) => doc.id === session.doc);
                    const node = course?.nodes.find((concept) => concept.id === session.focus);
                    return { id: session.id, title: node ? `${node.name} · ${course?.title}` : course?.title || "AI 讲解" };
                  })}
                  select={setGuideId}
                  onSelectConcept={(concept) => {
                    const existing = guideSessions.find((s) => s.doc === guideCourse.id && s.focus === concept.id);
                    if (existing) setGuideId(existing.id);
                    else void beginGuided(guideCourse, concept);
                  }}
                  starting={starting}
                  save={save} modelRequired={modelRequired} showReport={openReport}
                  graph={() => { setDoc(guideCourse.id); go("graph"); }} />
              ) : (
                <div className="chat-empty">
                  <span className="chat-avatar"><GraduationCap aria-hidden="true" /></span>
                  <h2>先听 AI 讲解</h2>
                  <p>{c ? "从图谱中选一个还没掌握的知识点，或直接从当前资料开启一课。" : "上传一份学习资料，生成图谱后开始讲解。"}</p>
                  {c && c.nodes.some((n) => n.score < 80) && (
                    <div className="tools" style={{ justifyContent: "center", marginTop: 8 }}>
                      {c.nodes.filter((n) => n.score < 80).slice(0, 3).map((n) => (
                        <button key={n.id} disabled={starting} onClick={() => void beginGuided(c, n)}>
                          <GraduationCap aria-hidden="true" /> {n.name}
                        </button>
                      ))}
                    </div>
                  )}
                  <button className="primary" onClick={() => go(c ? "graph" : "library")}>
                    {c ? "前往知识图谱" : "前往资料库"}
                  </button>
                </div>
              )
            ) : page === "studio" && sess ? (
              <Workbench
                openCapabilities={tab => online ? setCapabilities(tab || "skills") : setAuth(true)}
                openModels={() => setSettings(true)}
                openReview={() => { setDoc(sess.doc); go("review"); }}
                online={online}
                key={sess.id}
                enterAssessment={(id, course) => {
                  const assessment = newSession(id, course, course.nodes[0], sess.role);
                  save(s => ({...s, sessions: [{...assessment, targeted: true}, ...s.sessions]}));
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
                report={openReport}
              />
            ) : ["studio", "graph"].includes(page) ? (
              <div className="box empty">
                <PanelsTopLeft />

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
                guide={beginGuided}
                starting={starting}
                canLearn={canLearn}
                modelRequired={modelRequired}
                uploadTick={uploadTick}
                onOpenStudio={(id) => { setSid(id); go("studio"); }}
                onOpenTutor={(id) => { setGuideId(id); go("tutor"); }}
                onOpenGraph={(courseId) => { setDoc(courseId); go("graph"); }}
              />
            )}
          </Suspense>
        </div>
      </WorkspaceFrame>
      {settings && (
        <Suspense fallback={null}>
          <Settings
            key={user.id}
            online={online}
            close={() => setSettings(false)}
          />
        </Suspense>
      )}
      {capabilities && online && <Suspense fallback={null}><CapabilityCenter key={user.id} admin={user.admin} initialTab={capabilities} close={() => setCapabilities(false)} /></Suspense>}
      {auth && (
        <AccountDialog current={user} login={login} logout={() => logout()} close={() => setAuth(false)}
          onDeleted={() => {
            try {
              clearWorkspaceCache(user.id);
            } finally {
              logout("账号已删除。");
            }
          }} />
      )}
      {setup && c && (
        <Dialog
          title="开始费曼教学"
          className="teaching-setup"
          close={() => {
            if (!starting) setSetup(false);
          }}
        >
          {practiceSessions.some((s) => !s.closed) && (
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
                {practiceSessions
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
          <fieldset className="persona-picker">
            <legend>听众角色</legend>
            <div className="persona-options">
              {roles.map((r, i) => {
                const Icon = [Backpack, UsersRound, ClipboardCheck][i] || BookOpen;
                return <label className="persona" key={r}>
                  <input type="radio" className="persona-radio" name="listener" value={r}
                    aria-label={r} aria-describedby={"listener-hint-" + i}
                    checked={role === r} onChange={() => setRole(r)} />
                  <Icon aria-hidden="true" />
                  <strong>{r}</strong>
                  <small id={"listener-hint-" + i}>{["用生活例子讲明白", "一起推敲概念联系", "追问边界与依据"][i]}</small>
                </label>;
              })}
            </div>
          </fieldset>
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
                if (identity.current !== user.id) return;
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
    </div>
  );
}
document.documentElement.dataset.theme = storedTheme();
createRoot(document.getElementById("root")!).render(<App />);
