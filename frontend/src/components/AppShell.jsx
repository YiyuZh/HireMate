import { Link, NavLink, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../lib/api";

const navItems = [
  { to: "/jobs", label: "岗位配置" },
  { to: "/batch-screening", label: "批量初筛" },
  { to: "/workbench", label: "候选人工作台" },
  { to: "/admin/system-health", label: "系统健康" }
];

export default function AppShell({ title, subtitle, user, children, eyebrow = "HireMate" }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  async function handleLogout() {
    try {
      await api.post("/api/auth/logout", {});
    } finally {
      queryClient.clear();
      navigate("/login", { replace: true });
    }
  }

  return (
    <div className="min-h-screen bg-surface text-on-surface">
      <aside className="fixed left-0 top-0 flex h-screen w-72 flex-col bg-surface-container-low px-5 py-6">
        <div className="space-y-2">
          <Link to="/jobs" className="block font-headline text-3xl font-extrabold tracking-tight text-primary">
            HireMate
          </Link>
          <div className="rounded-2xl bg-surface-container-lowest p-4 shadow-ambient">
            <div className="text-xs uppercase tracking-[0.08em] text-on-surface-variant">当前用户</div>
            <div className="mt-2 font-semibold">{user?.name || user?.email}</div>
            <div className="mt-1 text-sm text-on-surface-variant">{user?.email}</div>
            <div className="mt-3 inline-flex rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">
              {user?.is_admin ? "管理员" : "招聘成员"}
            </div>
          </div>
        </div>

        <Link
          to="/jobs"
          className="mt-5 rounded-xl bg-primary px-4 py-3 text-center text-sm font-semibold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.12)] transition hover:bg-primary-container"
        >
          返回岗位库
        </Link>

        <nav className="mt-8 flex flex-1 flex-col gap-2">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `rounded-xl px-4 py-3 text-sm font-medium transition ${
                  isActive
                    ? "bg-surface-container-lowest text-primary shadow-ambient"
                    : "text-on-surface-variant hover:bg-surface-container-high hover:text-primary"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <button
          onClick={handleLogout}
          className="rounded-xl border border-outline-variant/30 bg-surface-container-lowest px-4 py-3 text-sm font-medium text-on-surface transition hover:border-primary/20 hover:text-primary"
        >
          退出登录
        </button>
      </aside>

      <div className="ml-72 min-h-screen">
        <header className="sticky top-0 z-20 border-b border-outline-variant/10 bg-surface/80 px-8 py-5 backdrop-blur-md">
          <div className="flex items-center justify-between gap-6">
            <div>
              <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{eyebrow}</div>
              <h1 className="mt-2 font-headline text-3xl font-extrabold tracking-tight">{title}</h1>
              {subtitle ? <p className="mt-2 max-w-3xl text-sm text-on-surface-variant">{subtitle}</p> : null}
            </div>
            <div className="flex items-center gap-3">
              <div className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-semibold text-emerald-700">
                主链路已连接
              </div>
            </div>
          </div>
        </header>

        <main className="p-8">{children}</main>
      </div>
    </div>
  );
}
