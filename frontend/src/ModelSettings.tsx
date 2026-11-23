import { useEffect, useState } from "react";
import { ChevronRight, Plus, SlidersHorizontal } from "lucide-react";
import {
  connected,
  modelSettingsApi as api,
  type ModelProfile,
  type ModelPurpose,
  type ModelSettings,
  type ProfileDraft,
} from "./api";
import { Dialog } from "./ui";

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

function Editor({
  profile,
  close,
  saved,
}: {
  profile?: ModelProfile;
  close: () => void;
  saved: () => Promise<void>;
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
      title={profile ? "编辑模型方案" : "新增模型方案"}
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
        <label>
          方案名称
          <input
            required
            maxLength={100}
            value={draft.name}
            onChange={(e) => field("name", e.target.value)}
            placeholder="如：教学主模型"
          />
        </label>
        <label>
          连接方式
          <select
            value={draft.mode}
            onChange={(e) => {
              const mode = e.target.value as ProfileDraft["mode"];
              setDraft({
                ...draft,
                mode,
                model: "",
                api_key: undefined,
                base_url: mode === "api" ? blank.base_url : "",
              });
              setChoices([]);
            }}
          >
            <option value="api">OpenAI 兼容 API</option>
            <option value="chatgpt">ChatGPT 登录</option>
          </select>
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
                onChange={(e) => field("api_key", e.target.value || undefined)}
                placeholder={
                  profile?.has_key
                    ? "已保存，留空沿用原密钥"
                    : "本机免密服务可留空"
                }
              />
            </label>
            {profile?.has_key && (
              <label>
                <span>
                  <input
                    type="checkbox"
                    checked={draft.api_key === ""}
                    onChange={(e) =>
                      field("api_key", e.target.checked ? "" : undefined)
                    }
                  />{" "}
                  清除已保存的密钥
                </span>
              </label>
            )}
          </>
        ) : (
          <p className="meta">使用本服务已授权的 ChatGPT 账号。</p>
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
        <div className="tools">
          <button type="button" disabled={busy} onClick={catalog}>
            获取模型目录
          </button>
          <button className="primary" disabled={busy}>
            保存方案
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export default function ModelSettingsPage({
  online,
  admin,
  login,
}: {
  online: boolean;
  admin: boolean;
  login: () => void;
}) {
  const [settings, setSettings] = useState<ModelSettings>(),
    [active, setActive] = useState<Record<string, string>>({}),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [editor, setEditor] = useState<ModelProfile | null>(),
    [remove, setRemove] = useState<ModelProfile>(),
    [authUrl, setAuthUrl] = useState("");
  async function reload() {
    if (admin) setSettings(await api.read());
    else setActive(await api.active());
  }
  useEffect(() => {
    if (online) void reload().catch((e) => setMessage(e.message));
  }, [online, admin]);
  useEffect(() => {
    if (!settings?.chatgpt.pending) return;
    let alive = true;
    const timer = setInterval(() => {
      api
        .read()
        .then((s) => {
          if (alive) {
            setSettings(s);
            if (!s.chatgpt.pending) setAuthUrl("");
          }
        })
        .catch((e) => {
          if (alive) setMessage(e.message);
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
    try {
      const feedback = await work();
      await reload();
      if (feedback) setMessage(feedback);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="pagehead">
        <div>
          <h1>模型配置</h1>
          <p>管理模型连接，并为教学环节选择模型。</p>
        </div>
        {online && admin && (
          <button className="primary" onClick={() => setEditor(null)}>
            <Plus />
            新增方案
          </button>
        )}
      </div>
      {message && <p role="status">{message}</p>}
      {!online ? (
        <section className="box">
          <h2>本地演示</h2>
          <p>
            登录服务端后可使用模型处理课件与教学对话。管理员可管理全站模型连接。
          </p>
          {!connected ? (
            <p>
              在项目根目录运行 <code>python dev.py</code> 后打开服务端工作台。
            </p>
          ) : (
            <button onClick={login}>
              账号登录
              <ChevronRight />
            </button>
          )}
        </section>
      ) : !admin ? (
        <section className="box">
          <h2>当前教学模型</h2>
          {Object.entries(active).map(([k, v]) => (
            <div className="record" key={k}>
              <strong>{purposes[k as ModelPurpose]}</strong>
              {v}
            </div>
          ))}
          <p className="meta">
            相关课程片段和学习记录会发送给该环节绑定的模型。
          </p>
        </section>
      ) : settings ? (
        <>
          <section className="box">
            <div className="row between">
              <h2>ChatGPT 账号</h2>
              <small>
                {settings.chatgpt.pending
                  ? "等待授权"
                  : settings.chatgpt.connected
                    ? "已登录"
                    : "未登录"}
              </small>
            </div>
            <p>在运行后端的电脑上完成浏览器授权，供 ChatGPT 类型的方案使用。</p>
            <div className="tools">
              {settings.chatgpt.pending ? (
                <>
                  {authUrl && (
                    <a href={authUrl} target="_blank" rel="noreferrer">
                      打开 ChatGPT 授权页 ↗
                    </a>
                  )}
                  <button
                    disabled={busy}
                    onClick={() =>
                      run(async () => {
                        await api.cancel();
                        setAuthUrl("");
                      })
                    }
                  >
                    取消登录
                  </button>
                </>
              ) : (
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      setAuthUrl((await api.login()).url);
                    })
                  }
                >
                  {settings.chatgpt.connected ? "重新授权" : "登录 ChatGPT"}
                </button>
              )}
              {settings.chatgpt.connected && (
                <button
                  disabled={busy}
                  onClick={() =>
                    run(async () => {
                      await api.logout();
                      setAuthUrl("");
                    })
                  }
                >
                  退出 ChatGPT
                </button>
              )}
            </div>
            {settings.chatgpt.error && (
              <p role="alert">{settings.chatgpt.error}</p>
            )}
          </section>
          <section className="box">
            <h2>模型方案</h2>
            {!settings.profiles.length && (
              <p>添加第一个方案后，它将成为默认教学模型。</p>
            )}
            {settings.profiles.map((p) => (
              <div key={p.id} className="record">
                <strong>
                  {p.name}
                  <small className="block">
                    {p.mode === "api" ? "API" : "ChatGPT"} · {p.model}
                  </small>
                </strong>
                <div className="tools">
                  <button
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
                  <button onClick={() => setEditor(p)}>编辑</button>
                  <button onClick={() => setRemove(p)}>删除</button>
                </div>
              </div>
            ))}
          </section>
          <section className="box">
            <h2>
              <SlidersHorizontal /> 应用绑定
            </h2>
            <p className="meta">
              此设置对服务端学习账号生效。相关课件片段和学习记录会发送给对应的模型服务。
            </p>
            {(Object.keys(purposes) as ModelPurpose[]).map((k) => (
              <div key={k} className="record">
                <strong>{purposes[k]}</strong>
                <select
                  aria-label={purposes[k]}
                  disabled={busy}
                  value={settings.bindings[k] || ""}
                  onChange={(e) => {
                    const id = e.target.value;
                    void run(async () => {
                      await api.bind(k, id);
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
          </section>
        </>
      ) : (
        <p>正在读取模型方案…</p>
      )}
      {editor !== undefined && (
        <Editor
          profile={editor || undefined}
          close={() => setEditor(undefined)}
          saved={reload}
        />
      )}
      {remove && (
        <Dialog title="删除模型方案" close={() => setRemove(undefined)}>
          <p>删除「{remove.name}」及其保存的密钥？</p>
          <button
            onClick={() => {
              void run(async () => {
                await api.remove(remove.id);
              });
              setRemove(undefined);
            }}
          >
            确认删除
          </button>
        </Dialog>
      )}
    </>
  );
}
