import { useEffect, useRef, useState } from "react";
import { ChevronRight, LockKeyhole, UserRound } from "lucide-react";
import { accountDeletePhrase, accountId, localAccountsApi, type LocalAccount } from "./api";
import type { User } from "./storage";
import { Dialog } from "./ui";

export default function LocalAccounts({
  current,
  login,
  close,
  logout,
  onDeleted,
}: {
  current?: User;
  login: (user: User) => void;
  close: () => void;
  logout?: () => void;
  onDeleted?: () => void;
}) {
  const [accounts, setAccounts] = useState<LocalAccount[]>();
  const [selected, setSelected] = useState<LocalAccount>();
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const active = accounts?.find(
    (account) => accountId(account.username) === current?.id,
  );

  async function run(work: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const refresh = () =>
    run(async () => setAccounts(await localAccountsApi.list()));
  useEffect(() => {
    void refresh();
  }, []);

  function activate(account: LocalAccount) {
    if (account.id === active?.id) {
      close();
    } else if (account.protected) {
      setError("");
      setValue("");
      setSelected(account);
    } else {
      void run(async () =>
        login(await localAccountsApi.activate(account.id, "")),
      );
    }
  }

  return (
    <Dialog
      title={deleting ? "删除当前账号" : "本机账号"}
      close={() => {
        if (!busy) close();
      }}
    >
      {deleting && current && onDeleted ? (
        <form onSubmit={(event) => {
          event.preventDefault();
          if (value === accountDeletePhrase) {
            void run(async () => {
              await localAccountsApi.removeCurrent(value);
              onDeleted();
            });
          }
        }}>
          <p>将永久删除「{current.name}」及其学习资料、图谱、进度和会话记录。</p>
          <label>
            请输入「{accountDeletePhrase}」
            <input autoFocus required autoComplete="off" spellCheck={false}
              value={value} disabled={busy} onChange={(event) => setValue(event.target.value)} />
          </label>
          <div className="tools">
            <button type="submit" className="primary" disabled={busy || value !== accountDeletePhrase}>
              永久删除账号
            </button>
            <button type="button" disabled={busy} onClick={() => {
              setDeleting(false);
              setValue("");
              setError("");
            }}>取消</button>
          </div>
        </form>
      ) : selected || creating ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void run(async () =>
              login(
                selected
                  ? await localAccountsApi.activate(selected.id, value)
                  : await localAccountsApi.create(value.trim()),
              ),
            );
          }}
        >
          {selected && <p>正在切换到「{selected.nickname}」。</p>}
          <label>
            {selected ? "密码" : "账号名称"}
            <input
              autoFocus
              required
              type={selected ? "password" : "text"}
              autoComplete={selected ? "current-password" : "off"}
              maxLength={selected ? 72 : 64}
              disabled={busy}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
          <div className="tools">
            <button className="primary" disabled={busy || !value.trim()}>
              {selected ? "切换账号" : "创建账号"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setSelected(undefined);
                setCreating(false);
                setError("");
              }}
            >
              返回
            </button>
          </div>
        </form>
      ) : (
        <>
          {accounts ? (
            <div className="local-accounts" aria-label="本机账号列表">
              {accounts.map((account) => (
                <button
                  type="button"
                  key={account.id}
                  disabled={busy}
                  onClick={() => activate(account)}
                >
                  <UserRound aria-hidden="true" />
                  <strong>{account.nickname}</strong>
                  {account.id === active?.id ? (
                    <small>当前账号</small>
                  ) : account.protected ? (
                    <LockKeyhole aria-label="需要密码" />
                  ) : (
                    <ChevronRight aria-hidden="true" />
                  )}
                </button>
              ))}
            </div>
          ) : error ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void refresh()}
            >
              重新读取账号
            </button>
          ) : (
            <p role="status">正在读取账号…</p>
          )}
          <div className="tools local-account-actions">
            <button
              className="primary"
              type="button"
              disabled={busy}
              onClick={() => {
                setError("");
                setValue("");
                setCreating(true);
              }}
            >
              新建账号
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => picker.current?.click()}
            >
              导入账号
            </button>
            {active && current && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void run(() => localAccountsApi.exportBackup(current.name))
                }
              >
                导出当前账号
              </button>
            )}
            {logout && <button type="button" disabled={busy} onClick={logout}>退出当前账号</button>}
            {active && onDeleted && (
              <button type="button" className="account-delete" disabled={busy} onClick={() => {
                setValue("");
                setError("");
                setDeleting(true);
              }}>删除当前账号</button>
            )}
            <input
              ref={picker}
              type="file"
              accept=".zip,application/zip"
              aria-label="选择账号备份"
              hidden
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                event.currentTarget.value = "";
                if (file)
                  void run(async () =>
                    login(await localAccountsApi.importBackup(file)),
                  );
              }}
            />
          </div>
          <p className="meta">导入会创建独立账号。备份包含学习资料和记录。</p>
        </>
      )}
      {error && <p role="alert">{error}</p>}
      {busy && accounts && <p role="status">正在处理…</p>}
    </Dialog>
  );
}
