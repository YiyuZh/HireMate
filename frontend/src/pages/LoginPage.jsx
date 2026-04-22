import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../lib/api";

export default function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ email: "", password: "" });
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function handleSubmit(event) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      await api.post("/api/auth/login", form);
      await queryClient.invalidateQueries({ queryKey: ["session"] });
      navigate("/jobs", { replace: true });
    } catch (err) {
      setError(err.message || "登录失败，请检查邮箱和密码。");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-6 py-10">
      <div className="grid w-full max-w-6xl grid-cols-1 overflow-hidden rounded-[28px] bg-surface-container-lowest shadow-ambient md:grid-cols-[1.05fr_0.95fr]">
        <section className="relative hidden min-h-[680px] flex-col justify-between overflow-hidden bg-gradient-to-br from-primary to-primary-container p-10 text-white md:flex">
          <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.36)_0%,rgba(255,255,255,0.08)_10%,transparent_26%)]" />
          <div className="relative z-10">
            <h1 className="font-headline text-6xl font-extrabold tracking-tight">HireMate</h1>
            <p className="mt-8 max-w-sm text-lg leading-9 text-white/80">
              Recruitment Screening
              <br />
              Candidate Review Workbench
            </p>
          </div>
          <div className="relative z-10 flex items-center justify-between">
            <div className="h-[6px] w-4/5 rounded-full bg-white/40 shadow-[0_0_18px_rgba(255,255,255,0.55)]" />
            <div className="rounded-2xl bg-white/90 px-5 py-3 text-xs font-bold uppercase tracking-[0.16em] text-primary">
              Secure Environment
            </div>
          </div>
        </section>

        <section className="flex min-h-[680px] flex-col justify-center bg-surface-container-lowest px-8 py-10 md:px-14">
          <div className="mb-10">
            <h2 className="font-headline text-5xl font-extrabold tracking-tight text-on-surface">进入工作台</h2>
            <p className="mt-3 text-sm text-on-surface-variant">
              输入企业账号后继续进入岗位配置、批量初筛和候选人工作台。
            </p>
          </div>

          {error ? (
            <div className="mb-6 rounded-2xl bg-error-container px-4 py-3 text-sm font-medium text-error">{error}</div>
          ) : null}

          <form className="space-y-5" onSubmit={handleSubmit}>
            <label className="block">
              <span className="mb-2 block text-xs font-semibold uppercase tracking-[0.12em] text-on-surface-variant">
                Corporate Email
              </span>
              <input
                type="email"
                value={form.email}
                onChange={(event) => setForm((prev) => ({ ...prev, email: event.target.value }))}
                placeholder="name@example.com"
                className="w-full rounded-2xl border-0 bg-surface-container-high px-4 py-4 text-sm outline-none ring-0 transition focus:bg-white focus:ring-2 focus:ring-primary"
              />
            </label>

            <label className="block">
              <span className="mb-2 block text-xs font-semibold uppercase tracking-[0.12em] text-on-surface-variant">
                Password
              </span>
              <input
                type="password"
                value={form.password}
                onChange={(event) => setForm((prev) => ({ ...prev, password: event.target.value }))}
                placeholder="请输入密码"
                className="w-full rounded-2xl border-0 bg-surface-container-high px-4 py-4 text-sm outline-none ring-0 transition focus:bg-white focus:ring-2 focus:ring-primary"
              />
            </label>

            <button
              type="submit"
              disabled={pending}
              className="w-full rounded-2xl bg-primary px-4 py-4 text-sm font-semibold text-white transition hover:bg-primary-container disabled:cursor-not-allowed disabled:opacity-70"
            >
              {pending ? "正在初始化..." : "进入工作台"}
            </button>
          </form>

          <div className="mt-8 flex items-center justify-center gap-2 text-xs text-on-surface-variant">
            <span className="relative flex h-2.5 w-2.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-70" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" />
            </span>
            <span>系统状态：主链路已连接</span>
          </div>
        </section>
      </div>
    </main>
  );
}
