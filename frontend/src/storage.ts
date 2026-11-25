import { Space, initial, roles } from "./model";
export type User = { id: string; name: string; admin: boolean };
export const student: User = {
    id: "demo-student",
    name: "演示同学",
    admin: false,
  },
  admin: User = { id: "demo-admin", name: "演示管理员", admin: true };
const prefix = "feynman.workspace.v1.";
const previousRoles = ["启蒙小学生", "互助同窗", "严苛考官"];
export function clearWorkspaceCache(id: string) {
  localStorage.removeItem(prefix + id);
  sessionStorage.removeItem("fm.active:" + id);
  sessionStorage.removeItem("fm.guided:" + id);
}

export function read(id: string): Space {
  const v = localStorage.getItem(prefix + id);
  try {
    if (v) {
      const space: Space = JSON.parse(v);
      return {
        ...space,
        sessions: space.sessions.map(session => ({
          ...session,
          role: roles[previousRoles.indexOf(session.role)] ?? session.role,
        })),
      };
    }
  } catch { /* A broken cache can be rebuilt. */ }
  return id === student.id
      ? initial()
      : { courses: [], reports: [], sessions: [], logs: [] };
}
export function write(id: string, s: Space) {
  // Server records are reloaded on login. Persist only edits that have not been sent.
  const stored: Space = id.startsWith("api:")
    ? {
        demoBuild: s.demoBuild,
        courses: s.courses.map((c) => ({ ...c, links: [], nodes: c.nodes.filter((n) => n.syncedNote !== undefined && n.note !== n.syncedNote) })).filter((c) => c.nodes.length),
        reports: [],
        logs: [],
        sessions: s.sessions
          .filter((session) => session.drafts.some(Boolean))
          .map((session) => ({ ...session, left: [], right: [] })),
      }
    : s;
  localStorage.setItem(prefix + id, JSON.stringify(stored));
}
export function spaces(): Record<string, Space> {
  return Object.fromEntries(
    Object.keys(localStorage)
      .filter((k) => k.startsWith(prefix) && !k.startsWith(prefix + "api:"))
      .map((k) => [
        k.slice(prefix.length),
        JSON.parse(localStorage.getItem(k)!),
      ]),
  );
}
type Account = {
  name: string;
  nickname: string;
  id: string;
  salt: string;
  hash: string;
};
function accounts(): Account[] {
  return JSON.parse(localStorage.getItem("feynman.accounts") || "[]");
}
export const userCount = () => accounts().length + 2;
async function derive(pass: string, salt: string) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(pass),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      salt: enc.encode(salt),
      iterations: 120000,
      hash: "SHA-256",
    },
    key,
    256,
  );
  return Array.from(new Uint8Array(bits)).join(".");
}
export async function authenticate(
  name: string,
  pass: string,
  nickname?: string,
): Promise<User> {
  const users = accounts();
  let a = users.find((x) => x.name === name);
  if (nickname !== undefined) {
    if (a) throw Error("用户名已被使用。");
    if (!nickname.trim() || !name.trim() || pass.length < 6)
      throw Error("请填写用户名、昵称和至少六位密码。");
    const salt = crypto.randomUUID();
    a = {
      name,
      nickname,
      id: crypto.randomUUID(),
      salt,
      hash: await derive(pass, salt),
    };
    localStorage.setItem("feynman.accounts", JSON.stringify([...users, a]));
  } else if (!a || a.hash !== (await derive(pass, a.salt)))
    throw Error("用户名或密码错误。");
  return { id: a.id, name: a.nickname, admin: false };
}
