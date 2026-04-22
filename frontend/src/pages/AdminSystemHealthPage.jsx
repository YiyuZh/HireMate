import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import AppShell from "../components/AppShell";
import { useSession } from "../components/RequireAuth";
import { api } from "../lib/api";
import { normalizeAdminHealth, normalizeAdminUsers } from "../lib/viewModels";

export default function AdminSystemHealthPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const [createForm, setCreateForm] = useState({
    email: "",
    name: "",
    password: "",
    is_admin: false
  });
  const [passwordDrafts, setPasswordDrafts] = useState({});
  const [banner, setBanner] = useState(null);
  const [rowFeedbacks, setRowFeedbacks] = useState({});

  const healthQuery = useQuery({
    queryKey: ["admin-health"],
    queryFn: async () => normalizeAdminHealth(await api.get("/api/admin/system-health"))
  });

  const usersQuery = useQuery({
    queryKey: ["admin-users"],
    queryFn: async () => normalizeAdminUsers(await api.get("/api/admin/users"))
  });

  function pushBanner(tone, title, detail = "") {
    setBanner({ tone, title, detail });
  }

  function pushRowFeedback(userId, tone, title, detail = "") {
    setRowFeedbacks((prev) => ({
      ...prev,
      [userId]: { tone, title, detail }
    }));
  }

  const createUserMutation = useMutation({
    mutationFn: () => api.post("/api/admin/users", createForm),
    onSuccess: async (payload) => {
      setCreateForm({ email: "", name: "", password: "", is_admin: false });
      pushBanner("success", "账号创建成功", `已创建 ${payload?.email || "新账号"}，默认状态为启用。`);
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
    onError: (error) => {
      pushBanner("error", "账号创建失败", error.message || "请检查邮箱是否重复，或补全必填项。");
    }
  });

  const toggleActiveMutation = useMutation({
    mutationFn: ({ userId, value }) => api.post(`/api/admin/users/${encodeURIComponent(userId)}/active`, { value }),
    onSuccess: async (_, variables) => {
      pushRowFeedback(
        variables.userId,
        "success",
        variables.value ? "账号已启用" : "账号已停用",
        variables.value ? "该账号可以重新登录系统。" : "该账号将无法继续登录系统。"
      );
      pushBanner("success", variables.value ? "启用成功" : "停用成功", "用户列表已刷新。");
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
    onError: (error, variables) => {
      pushRowFeedback(variables.userId, "error", "启停操作失败", error.message || "请稍后再试。");
      pushBanner("error", "账号启停失败", error.message || "请稍后再试。");
    }
  });

  const toggleAdminMutation = useMutation({
    mutationFn: ({ userId, value }) => api.post(`/api/admin/users/${encodeURIComponent(userId)}/admin`, { value }),
    onSuccess: async (_, variables) => {
      pushRowFeedback(
        variables.userId,
        "success",
        variables.value ? "已授予管理员权限" : "已取消管理员权限",
        "权限变更已即时生效。"
      );
      pushBanner("success", variables.value ? "管理员权限已授予" : "管理员权限已取消", "用户列表已刷新。");
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
    onError: (error, variables) => {
      pushRowFeedback(variables.userId, "error", "权限切换失败", error.message || "请稍后再试。");
      pushBanner("error", "管理员权限切换失败", error.message || "请稍后再试。");
    }
  });

  const resetPasswordMutation = useMutation({
    mutationFn: ({ userId, newPassword }) =>
      api.post(`/api/admin/users/${encodeURIComponent(userId)}/reset-password`, {
        new_password: newPassword
      }),
    onSuccess: async (_, variables) => {
      setPasswordDrafts((prev) => ({ ...prev, [variables.userId]: "" }));
      pushRowFeedback(variables.userId, "success", "密码已重置", "新密码已写入 hash，不会在页面回显明文。");
      pushBanner("success", "密码重置成功", "目标账号下次登录时请直接使用新密码。");
      await queryClient.invalidateQueries({ queryKey: ["admin-users"] });
    },
    onError: (error, variables) => {
      pushRowFeedback(variables.userId, "error", "密码重置失败", error.message || "请确认密码后重试。");
      pushBanner("error", "密码重置失败", error.message || "请确认密码后重试。");
    }
  });

  const users = usersQuery.data || [];
  const health = healthQuery.data || {
    database: { backend: "", ok: false, usersCount: 0, jobsCount: 0, batchesCount: 0 },
    ocr: { imageOcrAvailable: false, pdfOcrFallbackAvailable: false, details: {} },
    latestAiCall: { provider: "", model: "", apiBase: "", source: "", reason: "", envDetected: false }
  };

  const overviewStats = useMemo(
    () => [
      { label: "用户总数", value: health.database.usersCount },
      { label: "岗位总数", value: health.database.jobsCount },
      { label: "批次数", value: health.database.batchesCount }
    ],
    [health]
  );

  function handleCreateUser() {
    if (!createForm.email.trim() || !createForm.name.trim() || !createForm.password.trim()) {
      pushBanner("error", "创建失败", "邮箱、姓名和初始密码都是必填项。");
      return;
    }
    createUserMutation.mutate();
  }

  return (
    <AppShell
      user={session.data}
      eyebrow="Admin"
      title="系统健康与账号管理"
      subtitle="管理员可以在这里查看环境健康、创建内部账号，并完成密码重置、启停账号和管理员权限切换。"
    >
      <div className="space-y-6">
        {banner ? <FeedbackBanner {...banner} /> : null}

        <div className="grid gap-6 xl:grid-cols-[0.92fr_1.08fr]">
          <section className="space-y-6">
            <Panel eyebrow="Environment Health" title="系统健康总览">
              {healthQuery.isLoading ? (
                <EmptyState title="正在读取环境健康" description="请稍候，系统正在检查数据库、OCR 与最近一次 AI 调用状态。" />
              ) : (
                <>
                  <div className="grid gap-4 md:grid-cols-3">
                    {overviewStats.map((item) => (
                      <MetricCard key={item.label} label={item.label} value={item.value} />
                    ))}
                  </div>

                  <div className="mt-5 grid gap-4 md:grid-cols-2">
                    <StatusCard
                      title="数据库"
                      rows={[
                        ["backend", health.database.backend || "-"],
                        ["status", health.database.ok ? "healthy" : "degraded"]
                      ]}
                    />
                    <StatusCard
                      title="OCR"
                      rows={[
                        ["image OCR", health.ocr.imageOcrAvailable ? "available" : "missing"],
                        ["pdf fallback", health.ocr.pdfOcrFallbackAvailable ? "available" : "missing"]
                      ]}
                    />
                  </div>

                  <div className="mt-5 rounded-[20px] bg-surface-container-low p-4">
                    <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">最近一次 AI 调用</div>
                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      <QualityRow label="provider" value={health.latestAiCall.provider || "-"} />
                      <QualityRow label="model" value={health.latestAiCall.model || "-"} />
                      <QualityRow label="api_base" value={health.latestAiCall.apiBase || "-"} />
                      <QualityRow label="source" value={health.latestAiCall.source || "-"} />
                      <QualityRow label="env_detected" value={health.latestAiCall.envDetected ? "yes" : "no"} />
                      <QualityRow label="reason" value={health.latestAiCall.reason || "-"} />
                    </div>
                  </div>
                </>
              )}
            </Panel>

            <Panel eyebrow="Create Internal User" title="新建内部账号">
              <div className="grid gap-4">
                <Field label="邮箱">
                  <input
                    className="input-shell"
                    placeholder="name@example.com"
                    value={createForm.email}
                    onChange={(event) => setCreateForm((prev) => ({ ...prev, email: event.target.value }))}
                  />
                </Field>
                <Field label="姓名">
                  <input
                    className="input-shell"
                    placeholder="请输入姓名"
                    value={createForm.name}
                    onChange={(event) => setCreateForm((prev) => ({ ...prev, name: event.target.value }))}
                  />
                </Field>
                <Field label="初始密码">
                  <input
                    className="input-shell"
                    type="password"
                    placeholder="请输入初始密码"
                    value={createForm.password}
                    onChange={(event) => setCreateForm((prev) => ({ ...prev, password: event.target.value }))}
                  />
                </Field>

                <button
                  type="button"
                  onClick={() => setCreateForm((prev) => ({ ...prev, is_admin: !prev.is_admin }))}
                  className={`rounded-[20px] p-4 text-left transition ${
                    createForm.is_admin ? "bg-primary text-white" : "bg-surface-container-low text-on-surface"
                  }`}
                >
                  <div className="text-xs uppercase tracking-[0.12em] opacity-70">权限设置</div>
                  <div className="mt-2 font-semibold">{createForm.is_admin ? "创建为管理员" : "创建为普通成员"}</div>
                  <div className="mt-2 text-sm opacity-80">
                    {createForm.is_admin ? "该账号会拥有管理员能力。" : "该账号只用于招聘业务操作。"}
                  </div>
                </button>

                <button
                  onClick={handleCreateUser}
                  disabled={createUserMutation.isPending}
                  className="rounded-2xl bg-primary px-4 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {createUserMutation.isPending ? "创建中..." : "创建账号"}
                </button>
              </div>
            </Panel>
          </section>

          <section className="space-y-6">
            <Panel eyebrow="User Console" title="账号列表">
              {usersQuery.isLoading ? (
                <EmptyState title="正在读取账号列表" description="请稍候，系统正在加载当前管理员可管理的内部账号。" />
              ) : usersQuery.isError ? (
                <EmptyState title="账号列表读取失败" description="请检查登录状态，或稍后刷新后重试。" />
              ) : (
                <div className="space-y-4">
                  {users.map((user) => {
                    const rowFeedback = rowFeedbacks[user.userId];
                    const resetPending =
                      resetPasswordMutation.isPending && resetPasswordMutation.variables?.userId === user.userId;
                    const activePending =
                      toggleActiveMutation.isPending && toggleActiveMutation.variables?.userId === user.userId;
                    const adminPending =
                      toggleAdminMutation.isPending && toggleAdminMutation.variables?.userId === user.userId;

                    return (
                      <div key={user.userId} className="rounded-[24px] bg-surface-container-low p-5">
                        <div className="flex flex-wrap items-start justify-between gap-4">
                          <div>
                            <div className="font-semibold">{user.name || "未命名用户"}</div>
                            <div className="mt-1 text-sm text-on-surface-variant">{user.email}</div>
                            <div className="mt-2 text-xs text-on-surface-variant">
                              创建时间：{user.createdAt || "-"} · 最近登录：{user.lastLoginAt || "-"}
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-2 text-xs">
                            <span className="rounded-full bg-primary/10 px-3 py-1 text-primary">
                              {user.isAdmin ? "管理员" : "普通成员"}
                            </span>
                            <span
                              className={`rounded-full px-3 py-1 ${
                                user.isActive ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"
                              }`}
                            >
                              {user.isActive ? "启用中" : "已停用"}
                            </span>
                          </div>
                        </div>

                        {rowFeedback ? (
                          <InlineFeedback {...rowFeedback} />
                        ) : null}

                        <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto_auto]">
                          <input
                            type="password"
                            className="input-shell"
                            placeholder="输入新密码后点击重置"
                            value={passwordDrafts[user.userId] || ""}
                            onChange={(event) =>
                              setPasswordDrafts((prev) => ({ ...prev, [user.userId]: event.target.value }))
                            }
                          />
                          <button
                            onClick={() => {
                              const newPassword = passwordDrafts[user.userId] || "";
                              if (!newPassword.trim()) {
                                pushRowFeedback(user.userId, "error", "请先输入新密码", "密码为空时不会提交到后端。");
                                pushBanner("error", "密码重置未执行", "请先为目标账号输入新密码。");
                                return;
                              }
                              resetPasswordMutation.mutate({ userId: user.userId, newPassword });
                            }}
                            disabled={resetPending}
                            className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {resetPending ? "重置中..." : "重置密码"}
                          </button>
                          <button
                            onClick={() => toggleActiveMutation.mutate({ userId: user.userId, value: !user.isActive })}
                            disabled={activePending}
                            className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {activePending ? "处理中..." : user.isActive ? "停用账号" : "启用账号"}
                          </button>
                        </div>

                        <div className="mt-3 flex flex-wrap gap-3">
                          <button
                            onClick={() => toggleAdminMutation.mutate({ userId: user.userId, value: !user.isAdmin })}
                            disabled={adminPending}
                            className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {adminPending ? "处理中..." : user.isAdmin ? "取消管理员" : "设为管理员"}
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </Panel>
          </section>
        </div>
      </div>
    </AppShell>
  );
}

function Panel({ eyebrow, title, children }) {
  return (
    <div className="rounded-[24px] bg-surface-container-lowest p-6 shadow-ambient">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{eyebrow}</div>
      <div className="mt-2 font-headline text-2xl font-extrabold">{title}</div>
      <div className="mt-5">{children}</div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-2 block text-xs font-semibold uppercase tracking-[0.12em] text-on-surface-variant">
        {label}
      </span>
      {children}
    </label>
  );
}

function MetricCard({ label, value }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{label}</div>
      <div className="mt-3 font-headline text-3xl font-extrabold">{value}</div>
    </div>
  );
}

function StatusCard({ title, rows }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{title}</div>
      <div className="mt-3 space-y-2">
        {rows.map(([label, value]) => (
          <QualityRow key={`${title}-${label}`} label={label} value={value} />
        ))}
      </div>
    </div>
  );
}

function QualityRow({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-[14px] bg-white/70 px-3 py-2 text-sm">
      <span className="text-on-surface-variant">{label}</span>
      <span className="text-right text-on-surface">{String(value ?? "-")}</span>
    </div>
  );
}

function FeedbackBanner({ tone, title, detail }) {
  const toneClass =
    tone === "error"
      ? "bg-error-container text-error"
      : tone === "success"
        ? "bg-emerald-50 text-emerald-800"
        : "bg-amber-50 text-amber-800";
  return (
    <div className={`rounded-[20px] px-5 py-4 text-sm ${toneClass}`}>
      <div className="font-semibold">{title}</div>
      {detail ? <div className="mt-1">{detail}</div> : null}
    </div>
  );
}

function InlineFeedback({ tone, title, detail }) {
  return (
    <div
      className={`mt-4 rounded-[18px] px-4 py-3 text-sm ${
        tone === "error" ? "bg-error-container text-error" : "bg-emerald-50 text-emerald-800"
      }`}
    >
      <div className="font-semibold">{title}</div>
      {detail ? <div className="mt-1">{detail}</div> : null}
    </div>
  );
}

function EmptyState({ title, description }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4 text-sm text-on-surface-variant">
      <div className="font-semibold text-on-surface">{title}</div>
      <div className="mt-1 leading-7">{description}</div>
    </div>
  );
}
