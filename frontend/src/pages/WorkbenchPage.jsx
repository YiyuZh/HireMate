import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import AppShell from "../components/AppShell";
import { useSession } from "../components/RequireAuth";
import { api } from "../lib/api";
import {
  decisionLabels,
  formatConfidence,
  normalizeCandidateDetail,
  normalizeWorkbench,
  parseStatusLabels,
  poolLabels,
  priorityLabels,
  signedDelta
} from "../lib/viewModels";
import { useWorkbenchStore } from "../stores/workbenchStore";

const POOL_OPTIONS = [
  { value: "pending_review", label: poolLabels.pending_review },
  { value: "passed", label: poolLabels.passed },
  { value: "rejected", label: poolLabels.rejected }
];

const QUICK_FILTER_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "unreviewed", label: "仅看未人工处理" },
  { value: "high_priority", label: "仅看高优先级" },
  { value: "ai_not_generated", label: "仅看 AI 建议未生成" },
  { value: "ai_generated_not_applied", label: "仅看 AI 建议已生成但未应用" },
  { value: "ocr_weak", label: "仅看 OCR 弱质量 / OCR 能力缺失" },
  { value: "high_risk_pending_review", label: "仅看高风险且待复核" },
  { value: "self_locked", label: "仅看我处理中" },
  { value: "locked_by_other", label: "仅看他人锁定" },
  { value: "unlocked", label: "仅看未领取" }
];

const RISK_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "low", label: "low" },
  { value: "medium", label: "medium" },
  { value: "high", label: "high" },
  { value: "unknown", label: "unknown" }
];

const SORT_OPTIONS = [
  { value: "priority_desc", label: "处理优先级（高到低）" },
  { value: "priority_asc", label: "处理优先级（低到高）" },
  { value: "risk_desc", label: "风险等级（高到低）" },
  { value: "risk_asc", label: "风险等级（低到高）" }
];

const PRIORITY_OPTIONS = [
  { value: "high", label: priorityLabels.high },
  { value: "medium", label: priorityLabels.medium },
  { value: "normal", label: priorityLabels.normal },
  { value: "low", label: priorityLabels.low }
];

const DECISION_ACTIONS = [
  { value: "passed", label: decisionLabels.passed, tone: "success" },
  { value: "pending_review", label: decisionLabels.pending_review, tone: "warning" },
  { value: "rejected", label: decisionLabels.rejected, tone: "danger" }
];

const SUPPORT_STATUS_LABELS = {
  supported: "已证据支持",
  weakly_supported: "弱支持",
  contradicted: "存在反证",
  missing_evidence: "证据不足"
};

const RECOMMENDED_ACTION_LABELS = {
  proceed: "建议推进",
  manual_review: "建议人工复核",
  hold: "建议暂缓推进",
  reject: "建议暂不推荐",
  no_action: "不主动改动"
};

const ABSTAIN_REASON_LABELS = {
  manual_first: "解析质量偏弱，已优先人工复核",
  weak_text: "文本质量偏弱",
  missing_evidence: "缺少足够证据",
  counter_evidence_present: "存在反证或冲突信号",
  no_grounded_change: "没有通过 grounding 校验的可采纳改动"
};

export default function WorkbenchPage() {
  const session = useSession();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const batchId = searchParams.get("batch_id") || "";
  const jdTitle = searchParams.get("jd_title") || "";
  const { selectedCandidateId, setSelectedCandidateId, filters, setFilter } = useWorkbenchStore();
  const [manualNote, setManualNote] = useState("");
  const [manualPriority, setManualPriority] = useState("normal");
  const [selectedIds, setSelectedIds] = useState([]);
  const [bulkPriority, setBulkPriority] = useState("normal");
  const [feedback, setFeedback] = useState(null);

  const workbenchPath = useMemo(() => {
    const params = new URLSearchParams();
    if (batchId) {
      params.set("batch_id", batchId);
    }
    if (jdTitle) {
      params.set("jd_title", jdTitle);
    }
    params.set("pool", filters.pool);
    params.set("quick_filter", filters.quickFilter);
    params.set("search", filters.search);
    params.set("risk", filters.risk);
    params.set("sort", filters.sort);
    return `/api/workbench?${params.toString()}`;
  }, [batchId, jdTitle, filters]);

  const workbenchQuery = useQuery({
    queryKey: ["workbench", batchId, jdTitle, filters],
    queryFn: async () => normalizeWorkbench(await api.get(workbenchPath)),
    enabled: Boolean(batchId || jdTitle)
  });

  const rows = workbenchQuery.data?.rows || [];
  const batch = workbenchQuery.data?.batchSummary || {};
  const activeBatchId = batchId || batch.batchId || "";

  useEffect(() => {
    if (!rows.length) {
      if (selectedCandidateId) {
        setSelectedCandidateId("");
      }
      setSelectedIds([]);
      return;
    }
    const selectedStillVisible = rows.some((candidate) => candidate.candidateId === selectedCandidateId);
    if (!selectedCandidateId || !selectedStillVisible) {
      setSelectedCandidateId(rows[0].candidateId);
    }
    setSelectedIds((prev) => prev.filter((id) => rows.some((row) => row.candidateId === id)));
  }, [rows, selectedCandidateId, setSelectedCandidateId]);

  const detailQuery = useQuery({
    queryKey: ["candidate-detail", activeBatchId, selectedCandidateId],
    queryFn: async () =>
      normalizeCandidateDetail(
        await api.get(`/api/candidates/${encodeURIComponent(selectedCandidateId)}?batch_id=${encodeURIComponent(activeBatchId)}`)
      ),
    enabled: Boolean(activeBatchId && selectedCandidateId)
  });

  const detail = detailQuery.data;
  const activeRow = rows.find((candidate) => candidate.candidateId === selectedCandidateId) || null;
  const currentUserId = session.data?.user_id || "";

  useEffect(() => {
    if (!detail) {
      setManualNote("");
      setManualPriority("normal");
      return;
    }
    setManualNote(detail.manualReview.note || "");
    setManualPriority(detail.manualReview.priority || "normal");
  }, [detail?.identity?.candidateId, detail?.manualReview?.note, detail?.manualReview?.priority]);

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["workbench"] }),
      queryClient.invalidateQueries({ queryKey: ["candidate-detail"] })
    ]);
  }

  function pushFeedback(tone, title, detailText = "") {
    setFeedback({ tone, title, detail: detailText });
  }

  const noteMutation = useMutation({
    mutationFn: () =>
      api.post(
        `/api/candidates/${encodeURIComponent(selectedCandidateId)}/manual-note?batch_id=${encodeURIComponent(activeBatchId)}`,
        { note: manualNote }
      ),
    onSuccess: async () => {
      pushFeedback("success", "人工备注已保存", "这条备注会继续跟随候选人的人工审核留痕。");
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "人工备注保存失败", error.message || "请检查当前候选人是否已被其他 HR 锁定。");
    }
  });

  const priorityMutation = useMutation({
    mutationFn: () =>
      api.post(
        `/api/candidates/${encodeURIComponent(selectedCandidateId)}/manual-priority?batch_id=${encodeURIComponent(activeBatchId)}`,
        { priority: manualPriority }
      ),
    onSuccess: async () => {
      pushFeedback("success", "处理优先级已更新", `当前候选人已调整为“${priorityLabels[manualPriority] || manualPriority}”优先级。`);
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "处理优先级更新失败", error.message || "请稍后再试。");
    }
  });

  const decisionMutation = useMutation({
    mutationFn: (decision) =>
      api.post(
        `/api/candidates/${encodeURIComponent(selectedCandidateId)}/manual-decision?batch_id=${encodeURIComponent(activeBatchId)}`,
        { decision, note: manualNote }
      ),
    onSuccess: async (_, decision) => {
      pushFeedback("success", `人工结论已更新为“${decisionLabels[decision] || decision}”`, "规则评分仍会保留，最终推进动作依旧由人工确认。");
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "人工结论更新失败", error.message || "请检查当前候选人的锁定状态。");
    }
  });

  const claimMutation = useMutation({
    mutationFn: () =>
      api.post(
        `/api/candidates/${encodeURIComponent(selectedCandidateId)}/claim?batch_id=${encodeURIComponent(activeBatchId)}`,
        {}
      ),
    onSuccess: async (payload) => {
      pushFeedback(payload?.ok ? "success" : "warning", payload?.ok ? "候选人已领取" : "领取未成功", payload?.message || "");
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "领取失败", error.message || "请稍后重试。");
    }
  });

  const releaseMutation = useMutation({
    mutationFn: (force = false) =>
      api.post(
        `/api/candidates/${encodeURIComponent(selectedCandidateId)}/release?batch_id=${encodeURIComponent(activeBatchId)}&force=${force ? "true" : "false"}`,
        {}
      ),
    onSuccess: async (payload) => {
      pushFeedback(payload?.ok ? "success" : "warning", payload?.ok ? "锁定已释放" : "释放未成功", payload?.message || "");
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "释放失败", error.message || "请稍后再试。");
    }
  });

  const aiMutation = useMutation({
    mutationFn: () =>
      api.post(
        `/api/candidates/${encodeURIComponent(selectedCandidateId)}/ai/generate?batch_id=${encodeURIComponent(activeBatchId)}`,
        { force_refresh: true }
      ),
    onSuccess: async (payload) => {
      pushFeedback(
        payload?.ok === false ? "warning" : "success",
        payload?.ok === false ? "AI 建议未刷新" : "AI 建议已生成",
        payload?.message || "AI reviewer 仍是建议层，不会自动替代人工最终结论。"
      );
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "AI 建议生成失败", error.message || "系统已安全退化，但你仍可以继续人工审核。");
    }
  });

  const applyAiMutation = useMutation({
    mutationFn: (mode) =>
      api.post(
        `/api/candidates/${encodeURIComponent(selectedCandidateId)}/ai/apply?batch_id=${encodeURIComponent(activeBatchId)}`,
        mode === "all"
          ? {
              apply_evidence: true,
              apply_timeline: true,
              apply_risk: true,
              apply_scores: true
            }
          : {
              apply_evidence: true,
              apply_timeline: false,
              apply_risk: false,
              apply_scores: true
            }
      ),
    onSuccess: async (_, mode) => {
      pushFeedback(
        "success",
        mode === "all" ? "AI 建议已整体应用" : "AI 证据与评分建议已应用",
        "人工最终结论按钮仍然保留，AI 应用不会直接替代人工结论。"
      );
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "AI 建议应用失败", error.message || "请检查锁定状态或稍后重试。");
    }
  });

  const revertAiMutation = useMutation({
    mutationFn: () =>
      api.post(
        `/api/candidates/${encodeURIComponent(selectedCandidateId)}/ai/revert?batch_id=${encodeURIComponent(activeBatchId)}`,
        { full_restore: true }
      ),
    onSuccess: async () => {
      pushFeedback("success", "AI 应用结果已回退", "候选人已恢复到原始规则分析基线。");
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "AI 回退失败", error.message || "请稍后再试。");
    }
  });

  const bulkMutation = useMutation({
    mutationFn: ({ action, value = "" }) =>
      api.post(`/api/workbench/bulk-actions?batch_id=${encodeURIComponent(activeBatchId)}`, {
        action,
        candidate_ids: selectedIds,
        value
      }),
    onSuccess: async (payload, variables) => {
      pushFeedback(
        payload?.success_count > 0 ? "success" : "warning",
        "批量操作已执行",
        `${variables.action} 成功 ${payload?.success_count || 0} / ${payload?.total || selectedIds.length}。`
      );
      await refreshAll();
    },
    onError: (error) => {
      pushFeedback("error", "批量操作失败", error.message || "请检查所选候选人是否可操作。");
    }
  });

  function toggleSelected(id) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  }

  function selectVisible() {
    setSelectedIds(rows.map((row) => row.candidateId));
  }

  function clearSelected() {
    setSelectedIds([]);
  }

  const exportHref = useMemo(() => {
    const params = new URLSearchParams();
    if (activeBatchId) {
      params.set("batch_id", activeBatchId);
    }
    if (jdTitle) {
      params.set("jd_title", jdTitle);
    }
    params.set("pool", filters.pool);
    params.set("quick_filter", filters.quickFilter);
    params.set("search", filters.search);
    params.set("risk", filters.risk);
    params.set("sort", filters.sort);
    return `/api/exports/candidates.csv?${params.toString()}`;
  }, [activeBatchId, jdTitle, filters]);

  const requiresManualFirst =
    detail &&
    (["weak_text", "manual_first"].includes(String(detail.analysis.analysisMode || "")) ||
      String(detail.analysis.extractQuality || "").toLowerCase() === "weak");
  const lockState = detail?.lockState || activeRow?.lockState || defaultLockState();
  const selfLocked = Boolean(lockState.isLockedEffective && lockState.ownerUserId === currentUserId);
  const lockedByOther = Boolean(lockState.isLockedEffective && !selfLocked);

  const queueMetrics = [
    { label: "通过", value: batch.passCount ?? 0, tone: "emerald" },
    { label: "待复核", value: batch.reviewCount ?? 0, tone: "amber" },
    { label: "淘汰", value: batch.rejectCount ?? 0, tone: "rose" }
  ];

  const profile = detail?.analysis?.candidateProfile || {};
  const profileBlocks = [
    { label: "教育摘要", value: profile.education_summary || "未提取到稳定教育信息" },
    { label: "实习摘要", value: profile.internship_summary || "未提取到稳定实习信息" },
    { label: "项目摘要", value: profile.project_summary || "未提取到稳定项目信息" },
    { label: "技能清单", value: stringify(profile.skill_inventory) || "暂无稳定技能盘点" },
    { label: "岗位族群猜测", value: profile.role_family_guess || "待确认" },
    { label: "资历猜测", value: profile.seniority_guess || "待确认" },
    { label: "方法 / 产出 / 结果", value: stringify(profile.method_output_result_signals) || "暂无明显方法或结果信号" },
    { label: "缺失信息", value: stringify(profile.missing_info_points) || "暂无明显缺失项" }
  ];

  const actionDisabled = !selectedCandidateId || !activeBatchId;

  return (
    <AppShell
      user={session.data}
      eyebrow="Workbench"
      title="候选人工作台"
      subtitle="规则评分器仍是主链路，AI reviewer、analysis payload 和 evidence grounding 作为协同分析层辅助人工决策。"
    >
      <div className="grid gap-6 xl:grid-cols-[380px_minmax(0,1fr)]">
        <section className="space-y-5">
          <Panel
            eyebrow="Queue Overview"
            title={batch.jdTitle || jdTitle || "当前候选队列"}
            description="左侧保留高密度候选队列，右侧进入深度审核 cockpit。"
          >
            <div className="grid gap-3 sm:grid-cols-3">
              {queueMetrics.map((item) => (
                <QueueMetric key={item.label} label={item.label} value={item.value} tone={item.tone} />
              ))}
            </div>
            <div className="mt-4 rounded-[20px] bg-surface-container-low p-4 text-sm text-on-surface-variant">
              <div>当前批次：{batch.batchId || activeBatchId || "未指定"}</div>
              <div className="mt-1">岗位：{batch.jdTitle || jdTitle || "未指定"}</div>
              <div className="mt-1">当前列表：{rows.length} 位候选人</div>
            </div>
            <div className="mt-4 flex flex-wrap gap-3">
              <a href={exportHref} className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary">
                导出当前列表 CSV
              </a>
              <button
                onClick={() => refreshAll()}
                className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary"
              >
                刷新队列
              </button>
            </div>
          </Panel>

          <Panel eyebrow="Filter Rail" title="队列过滤器" tone="muted">
            <div className="grid gap-3">
              <Field label="候选池">
                <select value={filters.pool} onChange={(event) => setFilter("pool", event.target.value)} className="input-shell">
                  {POOL_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="快捷筛选">
                <select value={filters.quickFilter} onChange={(event) => setFilter("quickFilter", event.target.value)} className="input-shell">
                  {QUICK_FILTER_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>
              <div className="grid gap-3 md:grid-cols-2">
                <Field label="风险等级">
                  <select value={filters.risk} onChange={(event) => setFilter("risk", event.target.value)} className="input-shell">
                    {RISK_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label="排序方式">
                  <select value={filters.sort} onChange={(event) => setFilter("sort", event.target.value)} className="input-shell">
                    {SORT_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              <Field label="搜索候选人">
                <input
                  value={filters.search}
                  onChange={(event) => setFilter("search", event.target.value)}
                  placeholder="按姓名或文件名搜索"
                  className="input-shell"
                />
              </Field>
            </div>
          </Panel>

          <Panel eyebrow="Bulk Actions" title="批量操作">
            <div className="grid gap-3 text-sm text-on-surface-variant">
              <div>已勾选：{selectedIds.length} 人</div>
              <div>当前筛选结果：{rows.length} 人</div>
            </div>
            <div className="mt-4 flex flex-wrap gap-3">
              <button onClick={selectVisible} className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary">
                勾选当前筛选结果
              </button>
              <button onClick={clearSelected} className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary">
                清空勾选
              </button>
            </div>
            <div className="mt-4 grid gap-3">
              <button
                onClick={() => bulkMutation.mutate({ action: "manual_pending" })}
                disabled={!selectedIds.length || bulkMutation.isPending}
                className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                批量标记为待复核
              </button>
              <button
                onClick={() => bulkMutation.mutate({ action: "manual_reject" })}
                disabled={!selectedIds.length || bulkMutation.isPending}
                className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                批量标记为淘汰
              </button>
              <div className="grid gap-3 md:grid-cols-[1fr_auto]">
                <select value={bulkPriority} onChange={(event) => setBulkPriority(event.target.value)} className="input-shell">
                  {PRIORITY_OPTIONS.map((item) => (
                    <option key={item.value} value={item.value}>
                      {item.label}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => bulkMutation.mutate({ action: "set_priority", value: bulkPriority })}
                  disabled={!selectedIds.length || bulkMutation.isPending}
                  className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                >
                  应用优先级
                </button>
              </div>
              <button
                onClick={() => bulkMutation.mutate({ action: "generate_ai" })}
                disabled={!selectedIds.length || bulkMutation.isPending}
                className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                批量生成 AI 建议
              </button>
            </div>
          </Panel>

          <div className="space-y-4">
            {workbenchQuery.isLoading ? (
              <Panel eyebrow="Queue" title="正在加载候选队列" tone="muted">
                <div className="text-sm text-on-surface-variant">请稍候，系统正在整理当前批次的候选池。</div>
              </Panel>
            ) : null}

            {!workbenchQuery.isLoading && !rows.length ? (
              <Panel eyebrow="Queue" title="当前筛选结果为空" tone="muted">
                <div className="text-sm leading-7 text-on-surface-variant">
                  可以尝试切换候选池、风险筛选、快捷筛选或搜索条件，继续定位需要处理的候选人。
                </div>
              </Panel>
            ) : null}

            {rows.map((candidate) => {
              const active = candidate.candidateId === selectedCandidateId;
              const checked = selectedIds.includes(candidate.candidateId);
              return (
                <div
                  key={candidate.candidateId}
                  className={`rounded-[24px] p-5 transition ${
                    active
                      ? "bg-primary text-white shadow-ambient"
                      : "bg-surface-container-lowest text-on-surface shadow-ambient hover:-translate-y-0.5 hover:bg-white"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleSelected(candidate.candidateId)}
                      className="mt-1 h-4 w-4 rounded border-white/40"
                    />
                    <button
                      onClick={() => {
                        setSelectedCandidateId(candidate.candidateId);
                        setFeedback(null);
                      }}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className={`text-xs uppercase tracking-[0.12em] ${active ? "text-white/70" : "text-on-surface-variant"}`}>
                        {candidate.poolLabel}
                      </div>
                      <div className="mt-2 truncate font-headline text-2xl font-bold">
                        {candidate.name || candidate.fileName || "未命名候选人"}
                      </div>
                      <div className={`mt-1 truncate text-sm ${active ? "text-white/80" : "text-on-surface-variant"}`}>
                        {candidate.fileName || "未记录文件名"}
                      </div>

                      <div className="mt-4 flex flex-wrap gap-2">
                        <MiniBadge active={active}>{candidate.priorityLabel}</MiniBadge>
                        <MiniBadge active={active}>{candidate.parseStatusLabel}</MiniBadge>
                        <MiniBadge active={active}>{candidate.manualDecisionLabel}</MiniBadge>
                        <MiniBadge active={active}>{candidate.riskLevel}</MiniBadge>
                      </div>

                      <div className={`mt-4 text-sm leading-7 ${active ? "text-white/85" : "text-on-surface-variant"}`}>
                        {candidate.reviewSummary || "暂无审核摘要。"}
                      </div>

                      {candidate.lockState?.isLockedEffective ? (
                        <div className={`mt-3 text-xs ${active ? "text-white/70" : "text-on-surface-variant"}`}>
                          锁定人：{candidate.lockState.displayName}
                          {candidate.lockState.expiresAt ? ` · 截止 ${candidate.lockState.expiresAt}` : ""}
                        </div>
                      ) : null}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="space-y-6">
          {feedback ? <FeedbackBanner {...feedback} /> : null}

          {!detail || !activeRow ? (
            <Panel eyebrow="Cockpit" title="选择一个候选人开始审核">
              <EmptyState title="还没有激活候选人" description="从左侧列表选择一位候选人后，这里会展示结构化画像、证据链、AI 建议与人工决策面板。" />
            </Panel>
          ) : (
            <>
              <Panel eyebrow="Candidate Snapshot" title={detail.identity.name || detail.identity.fileName || "当前候选人"}>
                <div className="grid gap-3 md:grid-cols-2">
                  <InfoRow label="文件名" value={detail.identity.fileName || "-"} />
                  <InfoRow label="候选池" value={detail.identity.poolLabel} />
                  <InfoRow label="自动初筛结论" value={detail.identity.autoScreeningResultLabel || "-"} />
                  <InfoRow label="人工最终结论" value={detail.identity.manualDecisionLabel} />
                  <InfoRow label="风险等级" value={detail.risk.level || "-"} />
                  <InfoRow label="解析状态" value={detail.identity.parseStatusLabel} />
                </div>
                <div className="mt-4 rounded-[18px] bg-surface-container-low p-4 text-sm text-on-surface-variant">
                  {detail.identity.reviewSummary || "暂无审核摘要。"}
                </div>
                {requiresManualFirst ? (
                  <div className="mt-4 rounded-[18px] bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    当前 OCR / 解析质量偏弱，建议人工优先复核，再决定是否采纳 AI 建议。
                  </div>
                ) : null}
              </Panel>

              <Panel eyebrow="Lock State" title="协作与锁定状态">
                <div className="grid gap-3 md:grid-cols-2">
                  <InfoRow label="当前锁定状态" value={lockState.isLockedEffective ? "已锁定" : "未锁定"} />
                  <InfoRow label="锁定人" value={lockState.displayName} />
                </div>
                <div className="mt-4 flex flex-wrap gap-3">
                  <button
                    onClick={() => claimMutation.mutate()}
                    disabled={actionDisabled || claimMutation.isPending}
                    className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {claimMutation.isPending ? "领取中..." : "领取候选人"}
                  </button>
                  <button
                    onClick={() => releaseMutation.mutate(false)}
                    disabled={actionDisabled || releaseMutation.isPending}
                    className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {releaseMutation.isPending ? "释放中..." : "释放锁定"}
                  </button>
                  {session.data?.is_admin ? (
                    <button
                      onClick={() => releaseMutation.mutate(true)}
                      disabled={actionDisabled || releaseMutation.isPending}
                      className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      管理员强制释放
                    </button>
                  ) : null}
                  {lockedByOther ? (
                    <span className="rounded-full bg-rose-50 px-3 py-2 text-xs font-semibold text-rose-700">
                      当前由其他审核人锁定
                    </span>
                  ) : null}
                  {selfLocked ? (
                    <span className="rounded-full bg-emerald-50 px-3 py-2 text-xs font-semibold text-emerald-700">
                      当前由你处理中
                    </span>
                  ) : null}
                </div>
              </Panel>

              <Panel eyebrow="Confidence" title="解析质量与置信度">
                <div className="grid gap-4 md:grid-cols-3">
                  <MetricCard label="OCR 置信度" value={formatConfidence(detail.analysis.ocrConfidence)} />
                  <MetricCard label="结构置信度" value={formatConfidence(detail.analysis.structureConfidence)} />
                  <MetricCard label="解析置信度" value={formatConfidence(detail.analysis.parseConfidence)} />
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <InfoRow label="analysis_mode" value={detail.analysis.analysisMode || "normal"} />
                  {detail.analysis.analysisMode === "manual_first" || detail.analysis.abstainReasons.length ? (
                    <div className="md:col-span-2 rounded-[18px] border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-800">
                      <div className="font-semibold text-amber-900">
                        {"\u89e3\u6790\u8d28\u91cf\u9884\u8b66"}
                      </div>
                      <div className="mt-2 leading-7">
                        {detail.analysis.analysisMode === "manual_first"
                          ? "\u5f53\u524d OCR / \u89e3\u6790\u8d28\u91cf\u504f\u5f31\uff0c\u7cfb\u7edf\u5df2\u4f18\u5148\u6536\u53e3\u5230\u4eba\u5de5\u590d\u6838\u3002"
                          : "\u7cfb\u7edf\u68c0\u6d4b\u5230\u8bc1\u636e\u6216\u89e3\u6790\u4e0d\u8db3\uff0cAI \u5efa\u8bae\u5df2\u4fdd\u6301\u4fdd\u5b88\u8f93\u51fa\u3002"}
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {detail.analysis.abstainReasons.map((reason) => (
                          <MiniBadge key={`analysis-abstain-${reason}`}>{ABSTAIN_REASON_LABELS[reason] || reason}</MiniBadge>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <InfoRow label="提取方式" value={detail.analysis.extractMethod || "-"} />
                  <InfoRow label="提取质量" value={detail.analysis.extractQuality || "-"} />
                  <InfoRow label="提取说明" value={detail.analysis.extractMessage || "-"} />
                  <div className="md:col-span-2 grid gap-4 md:grid-cols-4">
                    <MetricCard label="正向证据" value={detail.evidence.positiveEvidence.length} />
                    <MetricCard label="反证" value={detail.evidence.counterEvidence.length} />
                    <MetricCard label="缺失点" value={detail.evidence.missingInfoPoints.length} />
                    <MetricCard label="Claim candidates" value={detail.analysis.claimCandidates.length} />
                  </div>
                </div>
              </Panel>

              <Panel eyebrow="Structured Profile" title="AI 结构化画像">
                <div className="grid gap-4 md:grid-cols-2">
                  {profileBlocks.map((item) => (
                    <ProfileCard key={item.label} label={item.label} value={item.value} />
                  ))}
                </div>
              </Panel>

              <Panel eyebrow="Evidence Chain" title="正向证据 / 反证 / 缺失点">
                <div className="grid gap-4 xl:grid-cols-3">
                  <EvidenceCard title="关键证据摘要" items={detail.evidence.summarySnippets} emptyText="暂无关键证据摘要。" />
                  <EvidenceCard title="正向证据" items={detail.evidence.positiveEvidence} emptyText="暂无正向证据。" />
                  <EvidenceCard title="反证与风险点" items={detail.evidence.counterEvidence} emptyText="暂无明显反证。" />
                </div>
                <div className="mt-4 rounded-[18px] border border-slate-200 bg-white/70 p-4 text-sm text-on-surface">
                  <div className="flex flex-wrap gap-2">
                    <MiniBadge active>
                      {RECOMMENDED_ACTION_LABELS[detail.aiReview.recommendedAction] || detail.aiReview.recommendedAction || "\u4e0d\u4e3b\u52a8\u6539\u52a8"}
                    </MiniBadge>
                    {detail.aiReview.recommendedActionDetail?.supportStatus ? (
                      <SupportBadge status={detail.aiReview.recommendedActionDetail.supportStatus} />
                    ) : null}
                    {detail.aiReview.recommendedActionDetail?.needsManualCheck ? (
                      <MiniBadge>{"\u5efa\u8bae\u4eba\u5de5\u590d\u6838"}</MiniBadge>
                    ) : null}
                  </div>
                  {detail.aiReview.recommendedActionDetail?.reason ? (
                    <div className="mt-3 leading-7 text-on-surface-variant">
                      {detail.aiReview.recommendedActionDetail.reason}
                    </div>
                  ) : null}
                  {detail.aiReview.abstainReasons.length ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {detail.aiReview.abstainReasons.map((reason) => (
                        <MiniBadge key={`ai-abstain-${reason}`}>{ABSTAIN_REASON_LABELS[reason] || reason}</MiniBadge>
                      ))}
                    </div>
                  ) : null}
                </div>

                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                  <EvidenceCard title="缺失信息点" items={detail.evidence.missingInfoPoints} emptyText="暂无明显缺失点。" />
                  <EvidenceCard title="时间线风险" items={detail.evidence.timelineRisks} emptyText="暂无明显时间线风险。" />
                </div>
              </Panel>

              <Panel eyebrow="Risk" title="风险复核建议">
                <div className="grid gap-3 md:grid-cols-2">
                  <InfoRow label="风险等级" value={detail.risk.level || "-"} />
                  <InfoRow label="风险摘要" value={detail.risk.summary || "-"} />
                </div>
                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                  <SimpleList title="风险点" items={detail.risk.points.map((item) => item.text || item.rawText)} emptyText="暂无风险点。" />
                  <SimpleList title="筛选原因" items={detail.risk.screeningReasons} emptyText="暂无筛选原因。" />
                </div>
              </Panel>

              <Panel eyebrow="AI Reviewer" title="AI 建议与采纳状态">
                <div className="grid gap-3 md:grid-cols-2">
                  <InfoRow label="状态" value={detail.aiReview.status || "not_generated"} />
                  <InfoRow label="来源" value={detail.aiReview.source || "-"} />
                  <InfoRow label="模型" value={detail.aiReview.model || "-"} />
                  <InfoRow label="生成时间" value={detail.aiReview.generatedAt || "-"} />
                </div>

                <div className="mt-4 flex flex-wrap gap-2">
                  {detail.aiReview.recommendedActionDetail?.supportStatus ? (
                    <SupportBadge status={detail.aiReview.recommendedActionDetail.supportStatus} />
                  ) : null}
                  {detail.aiReview.recommendedActionDetail?.needsManualCheck ? (
                    <MiniBadge active>{"\u5efa\u8bae\u4eba\u5de5\u590d\u6838"}</MiniBadge>
                  ) : null}
                  {detail.evidence.counterEvidence.length ? <MiniBadge>{"\u5b58\u5728\u53cd\u8bc1"}</MiniBadge> : null}
                  {detail.evidence.missingInfoPoints.length ? <MiniBadge>{"\u5b58\u5728\u7f3a\u8bc1"}</MiniBadge> : null}
                </div>

                <div className="mt-4 flex flex-wrap gap-3">
                  <button
                    onClick={() => aiMutation.mutate()}
                    disabled={actionDisabled || aiMutation.isPending || lockedByOther}
                    className="rounded-2xl bg-primary px-4 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {aiMutation.isPending ? "生成中..." : "生成 / 刷新 AI 建议"}
                  </button>
                  <button
                    onClick={() => applyAiMutation.mutate("scores")}
                    disabled={actionDisabled || applyAiMutation.isPending || lockedByOther}
                    className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    应用证据 + 评分
                  </button>
                  <button
                    onClick={() => applyAiMutation.mutate("all")}
                    disabled={actionDisabled || applyAiMutation.isPending || lockedByOther}
                    className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    应用全部建议
                  </button>
                  <button
                    onClick={() => revertAiMutation.mutate()}
                    disabled={actionDisabled || revertAiMutation.isPending || lockedByOther}
                    className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    回退 AI 应用
                  </button>
                </div>

                <div className="mt-4 rounded-[18px] bg-surface-container-low p-4 text-sm text-on-surface-variant">
                  <div className="font-semibold text-on-surface">AI 摘要</div>
                  <div className="mt-2 leading-7">{detail.aiReview.reviewSummary || "暂无 AI 摘要。"}</div>
                  {detail.aiReview.error ? <div className="mt-3 text-error">错误：{detail.aiReview.error}</div> : null}
                </div>

                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                  <SimpleList
                    title="建议追问问题"
                    items={detail.aiReview.interviewQuestions}
                    emptyText="暂无建议追问问题。"
                  />
                  <SimpleList title="建议关注点" items={detail.aiReview.focusPoints} emptyText="暂无建议关注点。" />
                </div>

                <div className="mt-4 grid gap-4 xl:grid-cols-2">
                  <ScoreAdjustmentCard items={detail.aiReview.scoreAdjustments} />
                  <div className="rounded-[18px] bg-surface-container-low p-4">
                    <div className="font-semibold text-on-surface">AI 建议采纳状态</div>
                    <div className="mt-3 space-y-2 text-sm text-on-surface-variant">
                      <InfoRow label="已采纳动作" value={detail.aiReview.appliedActions.join(" / ") || "暂无"} />
                      <InfoRow
                        label="风险建议"
                        value={detail.aiReview.riskAdjustment?.reason || detail.aiReview.riskAdjustment?.suggested_risk_level || "暂无"}
                      />
                    </div>
                  </div>
                </div>
              </Panel>

              <Panel eyebrow="Manual Review" title="人工最终决策">
                <div className="grid gap-5">
                  <Field label="人工备注">
                    <textarea
                      rows={5}
                      value={manualNote}
                      onChange={(event) => setManualNote(event.target.value)}
                      className="w-full rounded-[20px] bg-surface-container-low px-4 py-4 text-sm leading-7 outline-none focus:bg-white focus:ring-2 focus:ring-primary"
                    />
                  </Field>

                  <div className="grid gap-4 md:grid-cols-[1fr_auto]">
                    <Field label="处理优先级">
                      <select
                        value={manualPriority}
                        onChange={(event) => setManualPriority(event.target.value)}
                        className="input-shell"
                      >
                        {PRIORITY_OPTIONS.map((item) => (
                          <option key={item.value} value={item.value}>
                            {item.label}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <button
                      onClick={() => priorityMutation.mutate()}
                      disabled={actionDisabled || priorityMutation.isPending || lockedByOther}
                      className="self-end rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                    >
                      保存优先级
                    </button>
                  </div>

                  <button
                    onClick={() => noteMutation.mutate()}
                    disabled={actionDisabled || noteMutation.isPending || lockedByOther}
                    className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    保存人工备注
                  </button>

                  <div className="grid gap-3 md:grid-cols-3">
                    {DECISION_ACTIONS.map((item) => (
                      <button
                        key={item.value}
                        onClick={() => decisionMutation.mutate(item.value)}
                        disabled={actionDisabled || decisionMutation.isPending || lockedByOther}
                        className={`rounded-2xl px-4 py-3 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60 ${
                          item.tone === "success"
                            ? "bg-emerald-50 text-emerald-700"
                            : item.tone === "warning"
                              ? "bg-amber-50 text-amber-700"
                              : "bg-rose-50 text-rose-700"
                        }`}
                      >
                        标记为{item.label}
                      </button>
                    ))}
                  </div>
                </div>
              </Panel>
            </>
          )}
        </section>
      </div>
    </AppShell>
  );
}

function defaultLockState() {
  return {
    isLockedEffective: false,
    selfLocked: false,
    lockedByOther: false,
    ownerUserId: "",
    ownerName: "",
    ownerEmail: "",
    expiresAt: "",
    displayName: "未领取"
  };
}

function stringify(value) {
  if (!value) {
    return "";
  }
  if (Array.isArray(value)) {
    return value
      .map((item) => (typeof item === "string" ? item : item?.text || item?.label || JSON.stringify(item)))
      .filter(Boolean)
      .join(" / ");
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function Panel({ eyebrow, title, description, children, tone = "default" }) {
  return (
    <div className={`rounded-[24px] p-6 shadow-ambient ${tone === "muted" ? "bg-surface-container-low" : "bg-surface-container-lowest"}`}>
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{eyebrow}</div>
      <div className="mt-2 font-headline text-2xl font-extrabold">{title}</div>
      {description ? <div className="mt-2 text-sm leading-7 text-on-surface-variant">{description}</div> : null}
      <div className="mt-5">{children}</div>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-2 block text-xs font-semibold uppercase tracking-[0.12em] text-on-surface-variant">{label}</span>
      {children}
    </label>
  );
}

function QueueMetric({ label, value, tone }) {
  const toneClass =
    tone === "emerald" ? "text-emerald-700 bg-emerald-50" : tone === "amber" ? "text-amber-700 bg-amber-50" : "text-rose-700 bg-rose-50";
  return (
    <div className={`rounded-[20px] p-4 ${toneClass}`}>
      <div className="text-xs uppercase tracking-[0.12em] opacity-75">{label}</div>
      <div className="mt-3 font-headline text-3xl font-extrabold">{value}</div>
    </div>
  );
}

function MiniBadge({ children, active = false }) {
  return (
    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${active ? "bg-white/15 text-white" : "bg-surface-container-low text-on-surface-variant"}`}>
      {children}
    </span>
  );
}

function SupportBadge({ status }) {
  const toneClass =
    status === "supported"
      ? "bg-emerald-50 text-emerald-700"
      : status === "weakly_supported"
        ? "bg-sky-50 text-sky-700"
        : status === "contradicted"
          ? "bg-rose-50 text-rose-700"
          : "bg-amber-50 text-amber-800";
  return (
    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${toneClass}`}>
      {SUPPORT_STATUS_LABELS[status] || status || "证据状态未知"}
    </span>
  );
}

function FeedbackBanner({ tone, title, detail }) {
  const toneClass =
    tone === "error"
      ? "bg-error-container text-error"
      : tone === "warning"
        ? "bg-amber-50 text-amber-800"
        : "bg-emerald-50 text-emerald-800";
  return (
    <div className={`rounded-[20px] px-5 py-4 text-sm ${toneClass}`}>
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

function InfoRow({ label, value }) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-[16px] bg-surface-container-low px-4 py-3 text-sm">
      <span className="text-xs uppercase tracking-[0.08em] text-on-surface-variant">{label}</span>
      <span className="text-right text-on-surface">{String(value ?? "-")}</span>
    </div>
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

function ProfileCard({ label, value }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{label}</div>
      <div className="mt-3 text-sm leading-7 text-on-surface">{String(value || "-")}</div>
    </div>
  );
}

function EvidenceCard({ title, items, emptyText }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="font-semibold text-on-surface">{title}</div>
      {!items?.length ? (
        <div className="mt-3 text-sm text-on-surface-variant">{emptyText}</div>
      ) : (
        <div className="mt-3 space-y-3">
          {items.map((item, index) => (
            <div key={`${title}-${index}`} className="rounded-[16px] bg-white/80 p-3">
              <div className="flex flex-wrap gap-2">
                {item.label ? <MiniBadge>{item.label}</MiniBadge> : null}
                {item.tags?.map((tag) => (
                  <MiniBadge key={`${title}-${index}-${tag}`}>{tag}</MiniBadge>
                ))}
                {item.supportStatus ? <SupportBadge status={item.supportStatus} /> : null}
                {item.needsManualCheck ? <MiniBadge>{"\u5efa\u8bae\u4eba\u5de5\u590d\u6838"}</MiniBadge> : null}
                {item.groundedConfidence > 0 ? (
                  <MiniBadge>{`\u7f6e\u4fe1 ${Math.round(item.groundedConfidence * 100)}%`}</MiniBadge>
                ) : null}
              </div>
              <div className="mt-2 text-sm leading-7 text-on-surface">{item.text || item.rawText || "-"}</div>
              {item.isLowReadability ? (
                <div className="mt-2 text-xs text-amber-700">原文识别质量较弱，建议结合原始提取信息复核。</div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SimpleList({ title, items, emptyText }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="font-semibold text-on-surface">{title}</div>
      {!items?.length ? (
        <div className="mt-3 text-sm text-on-surface-variant">{emptyText}</div>
      ) : (
        <ul className="mt-3 space-y-2 text-sm leading-7 text-on-surface-variant">
          {items.map((item, index) => (
            <li key={`${title}-${index}`} className="rounded-[14px] bg-white/80 px-3 py-2 text-on-surface">
              {typeof item === "string" ? item : item?.text || item?.rawText || "-"}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ScoreAdjustmentCard({ items }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="font-semibold text-on-surface">评分挑战建议</div>
      {!items?.length ? (
        <div className="mt-3 text-sm text-on-surface-variant">暂无评分挑战建议。</div>
      ) : (
        <div className="mt-3 space-y-3">
          {items.map((item) => (
            <div key={`${item.dimension}-${item.reason}`} className="rounded-[16px] bg-white/80 p-3">
              <div className="flex items-center justify-between gap-3">
                <div className="font-semibold text-on-surface">{item.dimension || "未命名维度"}</div>
                <div className="rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">
                  {signedDelta(item.suggestedDelta)} / max {item.maxDelta}
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {item.supportStatus ? <SupportBadge status={item.supportStatus} /> : null}
                {item.needsManualCheck ? <MiniBadge>{"\u5efa\u8bae\u4eba\u5de5\u590d\u6838"}</MiniBadge> : null}
                {item.groundedConfidence > 0 ? (
                  <MiniBadge>{`\u7f6e\u4fe1 ${Math.round(item.groundedConfidence * 100)}%`}</MiniBadge>
                ) : null}
                {item.supportingEvidenceIds?.length ? (
                  <MiniBadge>{`\u6b63\u5411\u8bc1\u636e ${item.supportingEvidenceIds.length}`}</MiniBadge>
                ) : null}
                {item.opposingEvidenceIds?.length ? (
                  <MiniBadge>{`\u53cd\u8bc1 ${item.opposingEvidenceIds.length}`}</MiniBadge>
                ) : null}
              </div>
              <div className="mt-2 text-sm text-on-surface-variant">
                当前分数：{item.currentScore ?? "-"} · 理由：{item.reason || "暂无"}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
