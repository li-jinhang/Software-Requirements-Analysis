import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Folder } from "lucide-react";
import {
  connected,
  localModelSettings,
  modelSettingsApi as api,
  type ModelProfile,
  type ModelPurpose,
  type ModelSettings,
  type ProfileDraft,
} from "./api";
import { download } from "./model";
import { Dialog } from "./ui";
import "./settings.css";

const purposes: Record<ModelPurpose, string> = {
  default: "默认模型",
  extract: "资料解析",
  feynman: "费曼听众",
  encyclopedia: "百科助教",
  report: "诊断报告",
};
const blank: ProfileDraft = {
  name: "",
  mode: "api",
  base_url: "https://api.openai.com/v1",
  model: "",
};
const purposeKeys = Object.keys(purposes) as ModelPurpose[];

function Editor({
  profile,
  close,
  saved,
  account,
}: {
  profile?: ModelProfile;
  close: () => void;
  saved: () => Promise<void>;
  account: ReactNode;
}) {
  const [draft, setDraft] = useState<ProfileDraft>(
    profile
      ? {
          name: profile.name,
          base_url: profile.base_url,
          model: profile.model,
          mode: profile.mode,
        }
      : blank,
  );
  const [choices, setChoices] = useState<string[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  function field<K extends keyof ProfileDraft>(k: K, value: ProfileDraft[K]) {
    setDraft({ ...draft, [k]: value });
    setError("");
  }
  function changeMode(mode: ProfileDraft["mode"]) {
    if (mode === draft.mode) return;
    setDraft({
      ...draft,
      mode,
      model: "",
      api_key: undefined,
      base_url: mode === "api" ? blank.base_url : "",
    });
    setChoices([]);
    setError("");
  }
  async function catalog() {
    setBusy(true);
    setError("");
    try {
      const list = await api.catalog(
        { ...draft, name: draft.name || "新方案" },
        profile?.id,
      );
      setChoices(list);
      if (!list.length) setError("目录为空，可手动输入模型 ID。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={profile ? profile.name : "新建配置"}
      className="model-editor"
      close={() => {
        if (!busy) close();
      }}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError("");
          try {
            await api.save(draft, profile?.id);
            await saved();
            close();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="model-editor-modes" role="group" aria-label="连接方式">
          <button
            type="button"
            aria-pressed={draft.mode === "api"}
            disabled={busy}
            onClick={() => changeMode("api")}
          >
            OpenAI 兼容 API
          </button>
          <button
            type="button"
            aria-pressed={draft.mode === "chatgpt"}
            disabled={busy}
            onClick={() => changeMode("chatgpt")}
          >
            ChatGPT 登录
          </button>
        </div>
        <label>
          配置名称
          <input
            required
            maxLength={100}
            value={draft.name}
            onChange={(e) => field("name", e.target.value)}
            placeholder="如：教学主模型"
          />
        </label>
        {draft.mode === "api" ? (
          <>
            <label>
              Base URL
              <input
                required
                type="url"
                value={draft.base_url}
                onChange={(e) => {
                  field("base_url", e.target.value);
                  setChoices([]);
                }}
              />
            </label>
            <label>
              API Key
              <input
                type="password"
                autoComplete="new-password"
                value={draft.api_key || ""}
                onChange={(e) => {
                  field("api_key", e.target.value || undefined);
                  setChoices([]);
                }}
                placeholder={
                  profile?.has_key
                    ? "已保存，留空沿用原密钥"
                    : "本机免密服务可留空"
                }
              />
            </label>
            {profile?.has_key && (
              <label className="model-key-clear">
                <input
                  type="checkbox"
                  checked={draft.api_key === ""}
                  onChange={(e) =>
                    field("api_key", e.target.checked ? "" : undefined)
                  }
                />
                清除已保存的密钥
              </label>
            )}
          </>
        ) : (
          account
        )}
        <label>
          模型 ID
          <input
            required
            maxLength={200}
            list="model-choices"
            value={draft.model}
            onChange={(e) => field("model", e.target.value)}
            placeholder="手动输入，或从目录选择"
          />
        </label>
        <datalist id="model-choices">
          {choices.map((id) => (
            <option key={id} value={id} />
          ))}
        </datalist>
        {choices.length > 0 && (
          <label>
            服务端目录
            <select
              aria-label="服务端模型目录"
              value={choices.includes(draft.model) ? draft.model : ""}
              onChange={(e) => field("model", e.target.value)}
            >
              <option value="">选择模型</option>
              {choices.map((id) => (
                <option key={id}>{id}</option>
              ))}
            </select>
          </label>
        )}
        {error && <p role="alert">{error}</p>}
        <div className="tools model-editor-actions">
          <button type="button" disabled={busy} onClick={catalog}>
            获取模型目录
          </button>
          <button className="primary" disabled={busy}>
            保存
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function ChatGPTAccount({
  state,
  url,
  setUrl,
  reload,
}: {
  state: ModelSettings["chatgpt"];
  url: string;
  setUrl: (url: string) => void;
  reload: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function authorize(work: () => Promise<string>) {
    setBusy(true);
    setError("");
    try {
      setUrl(await work());
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="model-account" aria-label="ChatGPT 账号">
      <strong>ChatGPT 账号</strong>
      <p role="status">
        {state.pending ? "等待授权" : state.connected ? "已登录" : "尚未登录"}
      </p>
      <p className="meta">请在运行后端的电脑上完成浏览器授权。</p>
      <div className="tools">
        {state.pending ? (
          <>
            {url && (
              <a href={url} target="_blank" rel="noreferrer">
                打开 ChatGPT 授权页 ↗
              </a>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                authorize(async () => {
                  await api.cancel();
                  return "";
                })
              }
            >
              取消登录
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => authorize(async () => (await api.login()).url)}
          >
            {state.connected ? "重新授权" : "登录 ChatGPT"}
          </button>
        )}
        {state.connected && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              authorize(async () => {
                await api.logout();
                return "";
              })
            }
          >
            退出 ChatGPT
          </button>
        )}
      </div>
      {(error || state.error) && <p role="alert">{error || state.error}</p>}
    </section>
  );
}

export default function Settings({
  online,
  close,
}: {
  online: boolean;
  close: () => void;
}) {
  const [category, setCategory] = useState<"models" | "teaching">("models");
  const [expanded, setExpanded] = useState(true);
  const profileListId = useId();
  const archivePicker = useRef<HTMLInputElement>(null);
  const [settings, setSettings] = useState<ModelSettings>(),
    [active, setActive] = useState<Awaited<ReturnType<typeof api.active>>>(),
    [message, setMessage] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [editor, setEditor] = useState<ModelProfile | null>(),
    [remove, setRemove] = useState<ModelProfile>(),
    [authUrl, setAuthUrl] = useState("");
  async function reload() {
    if (localModelSettings) setSettings(await api.read());
    else if (online) setActive(await api.active());
  }
  useEffect(() => {
    void reload().catch((e) => setError(e.message));
  }, [online]);
  useEffect(() => {
    if (!settings?.chatgpt.pending) return;
    let alive = true;
    const timer = setInterval(() => {
      api
        .read()
        .then(({ chatgpt }) => {
          if (!alive) return;
          setSettings((s) => s && { ...s, chatgpt });
          if (!chatgpt.pending) setAuthUrl("");
        })
        .catch((e) => {
          if (alive) setError(e.message);
        });
    }, 1600);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [settings?.chatgpt.pending]);
  async function run(work: () => Promise<string | void>) {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const feedback = await work();
      await reload();
      if (feedback) setMessage(feedback);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function readArchive(file: File) {
    const text = await file.text();
    let archive: unknown;
    try {
      archive = JSON.parse(text.replace(/^\uFEFF/, ""));
    } catch {
      throw Error("文件不是有效的 JSON，请选择导出的模型配置文件。");
    }
    const { added, skipped, bindings_added } = await api.importArchive(archive);
    setExpanded(true);
    return `已导入 ${added} 个配置，保留 ${skipped} 个已有配置，补充 ${bindings_added} 项绑定。`;
  }

  return (
    <Dialog title="设置" close={close} className="settings-dialog">
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="设置栏目">
          {localModelSettings && (
            <button
              type="button"
              aria-current={category === "teaching" ? "page" : undefined}
              onClick={() => setCategory("teaching")}
            >
              教学设置
            </button>
          )}
          <button
            type="button"
            aria-current={category === "models" ? "page" : undefined}
            onClick={() => setCategory("models")}
          >
            模型配置
          </button>
        </nav>
        <section
          className="settings-panel"
          aria-labelledby="settings-panel-title"
        >
          <div className="settings-panel-heading">
            <h3 id="settings-panel-title">
              {category === "models" ? "模型配置" : "教学设置"}
            </h3>
            {category === "models" && settings && (
              <div className="tools">
                <input
                  ref={archivePicker}
                  type="file"
                  accept=".json,application/json"
                  aria-label="选择模型配置文件"
                  hidden
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0];
                    event.currentTarget.value = "";
                    if (file) void run(() => readArchive(file));
                  }}
                />
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => archivePicker.current?.click()}
                >
                  导入配置
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      const archive = await api.exportArchive();
                      download(
                        "feynman-models.json",
                        JSON.stringify(archive, null, 2),
                        "application/json",
                      );
                    })
                  }
                >
                  导出配置
                </button>
              </div>
            )}
          </div>
          {message && <p role="status">{message}</p>}
          {error && <p role="alert">{error}</p>}
          {!localModelSettings && !online && (
            <div className="settings-empty">
              <strong>本机模型设置</strong>
              <p>
                {connected ? (
                  "请在运行服务的电脑上打开本机工作台。"
                ) : (
                  <>
                    双击 <code>run.bat</code> 或运行 <code>python dev.py</code>{" "}
                    后打开本机工作台。
                  </>
                )}
              </p>
            </div>
          )}
          {!localModelSettings && !online ? null : category === "teaching" &&
            settings ? (
            <>
              <h4 className="settings-subtitle">应用绑定</h4>
              <p className="meta">
                本机教学服务使用这里的模型。相关课件片段和学习记录会发送给对应的模型服务。
              </p>
              <div className="settings-bindings">
                {purposeKeys.map((k) => (
                  <div key={k} className="settings-binding">
                    <label htmlFor={"model-binding-" + k}>{purposes[k]}</label>
                    <select
                      id={"model-binding-" + k}
                      disabled={busy}
                      value={settings.bindings[k] || ""}
                      onChange={(e) => {
                        const id = e.target.value;
                        void run(async () => {
                          await api.bind(k, id);
                          return "已更新" + purposes[k];
                        });
                      }}
                    >
                      <option value="">
                        {k === "default"
                          ? settings.environment_live
                            ? "使用环境配置"
                            : "离线演示"
                          : "继承默认模型"}
                      </option>
                      {settings.profiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name} · {p.model}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            </>
          ) : settings ? (
            <section className="model-group" aria-labelledby="language-models">
              <p className="meta settings-archive-hint">
                导入会合并方案和绑定。API Key 与 ChatGPT 授权仅保存在本机。
              </p>
              <div className="model-group-heading">
                <h4>
                  <button
                    id="language-models"
                    type="button"
                    className="quiet"
                    aria-expanded={expanded}
                    aria-controls={profileListId}
                    onClick={() => setExpanded(!expanded)}
                  >
                    {expanded ? (
                      <ChevronDown aria-hidden="true" />
                    ) : (
                      <ChevronRight aria-hidden="true" />
                    )}
                    <Folder aria-hidden="true" />
                    语言模型
                  </button>
                </h4>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setExpanded(true);
                    setEditor(null);
                  }}
                >
                  新建配置
                </button>
              </div>
              <div
                id={profileListId}
                className="model-group-items"
                hidden={!expanded}
              >
                {!settings.profiles.length && (
                  <p className="meta">首个配置会自动设为默认模型。</p>
                )}
                {settings.profiles.map((p) => {
                  const uses = purposeKeys.filter(
                    (k) => settings.bindings[k] === p.id,
                  );
                  const summary =
                    (p.mode === "api" ? p.base_url : "ChatGPT") +
                    " · " +
                    p.model;
                  return (
                    <article
                      key={p.id}
                      className="model-card"
                      data-active={uses.length > 0}
                    >
                      <div className="model-identity">
                        <span className="model-icon" aria-hidden="true">
                          {p.mode === "chatgpt" ? "CH" : "API"}
                        </span>
                        <div className="model-info">
                          <strong>{p.name}</strong>
                          <small>{summary}</small>
                        </div>
                      </div>
                      <div className="model-actions">
                        {uses.length > 0 && (
                          <span
                            className="model-active"
                            title={uses.map((k) => purposes[k]).join("、")}
                          >
                            {uses.includes("default")
                              ? "默认使用中"
                              : "教学使用中"}
                          </span>
                        )}
                        <button
                          type="button"
                          className="quiet"
                          disabled={busy}
                          onClick={() =>
                            run(
                              async () =>
                                p.name +
                                " 连接成功 · " +
                                (await api.test(p.id)).latency_ms +
                                " ms",
                            )
                          }
                        >
                          测试连接
                        </button>
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => setEditor(p)}
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          className="quiet"
                          disabled={busy}
                          onClick={() => setRemove(p)}
                        >
                          删除
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          ) : active ? (
            <>
              <h4 className="settings-subtitle">当前教学模型</h4>
              <div className="settings-bindings">
                {Object.entries(active).map(([k, v]) => (
                  <div className="settings-binding" key={k}>
                    <strong>{purposes[k as ModelPurpose]}</strong>
                    <span>{v}</span>
                  </div>
                ))}
              </div>
              <p className="meta">
                相关课件片段和学习记录会发送给相应的模型服务。
              </p>
            </>
          ) : error ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => run(async () => {})}
            >
              重新加载
            </button>
          ) : (
            <p role="status">正在读取模型配置…</p>
          )}
        </section>
      </div>
      {editor !== undefined && settings && (
        <Editor
          profile={editor || undefined}
          close={() => setEditor(undefined)}
          saved={reload}
          account={
            <ChatGPTAccount
              state={settings.chatgpt}
              url={authUrl}
              setUrl={setAuthUrl}
              reload={reload}
            />
          }
        />
      )}
      {remove && (
        <Dialog title="删除模型配置" close={() => setRemove(undefined)}>
          <p>删除「{remove.name}」及其保存的密钥？</p>
          <div className="tools model-editor-actions">
            <button type="button" onClick={() => setRemove(undefined)}>
              取消
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => {
                void run(async () => {
                  await api.remove(remove.id);
                  return "已删除「" + remove.name + "」";
                });
                setRemove(undefined);
              }}
            >
              确认删除
            </button>
          </div>
        </Dialog>
      )}
    </Dialog>
  );
}
