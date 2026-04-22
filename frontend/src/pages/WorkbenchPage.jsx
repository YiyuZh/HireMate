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
  { value: "ocr_weak", label: "仅看 OCR 弱质量 / OCR 缺失" },
  { value: "high_risk_pending_review", label: "仅看高风险待复核" },
  { value: "self_locked", label: "仅看我处理中" },
  { value: "locked_by_other", label: "仅看他人锁定" },
  { value: "unlocked", label: "仅看未领取" }
];

const RISK_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "low", label: "低风险" },
  { value: "medium", label: "中风险" },
  { value: "high", label: "高风险" },
  { value: "unknown", label: "未知" }
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
  manual_first: "解析质量偏弱，建议人工优先复核",
  weak_text: "文本质量偏弱",
  missing_evidence: "缺少足够证据",
  counter_evidence_present: "存在反证或冲突信息",
  no_grounded_change: "没有通过 grounding 校验的改动"
};

const PROFILE_BLOCKS = [
  { key: "education_summary", label: "教育摘要" },
  { key: "internship_summary", label: "实习摘要" },
  { key: "project_summary", label: "项目摘要" },
  { key: "skill_inventory", label: "技能清单" },
  { key: "role_family_guess", label: "岗位族群猜测" },
  { key: "seniority_guess", label: "资历猜测" },
  { key: "method_output_result_signals", label: "方法 / 产出 / 结果" },
  { key: "timeline_risks", label: "时间线风险" },
  { key: "missing_info_points", label: "缺失信息" }
];

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
      pushFeedback(payload?.ok ? "success" : "warning", payload?.ok ? "候选人已领取" : "领取未完成", payload?.message || "");
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
      pushFeedback(payload?.ok ? "success" : "warning", payload?.ok ? "锁定已释放" : "释放未完成", payload?.message || "");
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
        payload?.message || "AI reviewer 仍然只是建议层，不会自动替代人工最终结论。"
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
  const actionDisabled = !selectedCandidateId || !activeBatchId;

  const heroStats = [
    { label: "候选总数", value: batch.totalResumes ?? rows.length, tone: "slate" },
    { label: "通过", value: batch.passCount ?? 0, tone: "emerald" },
    { label: "待复核", value: batch.reviewCount ?? 0, tone: "amber" },
    { label: "淘汰", value: batch.rejectCount ?? 0, tone: "rose" }
  ];

  const profile = detail?.analysis?.candidateProfile || {};

  return (
    <AppShell
      user={session.data}
      eyebrow="Workbench"
      title="候选人工作台"
      subtitle="规则评分器仍是主链路，AI reviewer、analysis payload 和 evidence grounding 作为协同分析层辅助人工决策。"
    >
      <div className="space-y-6">
        <section className="rounded-[28px] bg-surface-container-lowest p-8 shadow-ambient">
          <div className="flex flex-col gap-6 xl:flex-row xl:items-end xl:justify-between">
            <div className="space-y-3">
              <div className="text-xs uppercase tracking-[0.16em] text-on-surface-variant">Workbench Cockpit</div>
              <h2 className="font-headline text-4xl font-extrabold tracking-tight text-on-surface">候选队列与深度审核 cockpit</h2>
              <p className="max-w-3xl text-sm leading-7 text-on-surface-variant">
                左侧专注筛选与队列切换，中间维护高密度候选队列，右侧承载当前候选人的结构化画像、证据链、AI 建议与人工最终决策。
              </p>
              <div className="flex flex-wrap gap-2">
                <InlineBadge>{batch.batchId ? `批次 ${batch.batchId}` : "未指定批次"}</InlineBadge>
                <InlineBadge>{batch.jdTitle || jdTitle || "未指定岗位"}</InlineBadge>
                <InlineBadge tone="success">主链路已连接</InlineBadge>
              </div>
            </div>

            <div className="flex flex-wrap gap-3">
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
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {heroStats.map((item) => (
              <HeroMetricCard key={item.label} {...item} />
            ))}
          </div>
        </section>

        <div className="grid gap-6 xl:grid-cols-[300px_380px_minmax(0,1fr)] 2xl:grid-cols-[320px_420px_minmax(0,1fr)]">
          <aside className="space-y-6 xl:sticky xl:top-28 self-start">
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
                  <select
                    value={filters.quickFilter}
                    onChange={(event) => setFilter("quickFilter", event.target.value)}
                    className="input-shell"
                  >
                    {QUICK_FILTER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </Field>

                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
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
                <div className="grid gap-3">
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
          </aside>

          <section className="space-y-4 min-w-0">
            <Panel
              eyebrow="Queue Rail"
              title={batch.jdTitle || jdTitle || "当前候选队列"}
              description="中间列固定承载候选队列，避免筛选区和详情区抢占同一列宽度。"
            >
              <div className="grid gap-3 sm:grid-cols-3">
                <QueueStat label="当前池" value={poolLabels[filters.pool] || filters.pool} />
                <QueueStat label="搜索命中" value={`${rows.length} 人`} />
                <QueueStat label="已勾选" value={`${selectedIds.length} 人`} />
              </div>
            </Panel>

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

            {rows.map((candidate) => (
              <QueueCard
                key={candidate.candidateId}
                candidate={candidate}
                active={candidate.candidateId === selectedCandidateId}
                checked={selectedIds.includes(candidate.candidateId)}
                onToggleChecked={() => toggleSelected(candidate.candidateId)}
                onSelect={() => {
                  setSelectedCandidateId(candidate.candidateId);
                  setFeedback(null);
                }}
              />
            ))}
          </section>

          <section className="space-y-6 min-w-0">
            {feedback ? <FeedbackBanner {...feedback} /> : null}

            {!detail || !activeRow ? (
              <Panel eyebrow="Cockpit" title="选择一位候选人开始审核">
                <EmptyState
                  title="还没有激活候选人"
                  description="从中间队列中选择一位候选人后，这里会展示结构化画像、证据链、AI 建议与人工最终决策。"
                />
              </Panel>
            ) : (
              <>
                <Panel eyebrow="Candidate Cockpit" title={detail.identity.name || detail.identity.fileName || "未命名候选人"}>
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
                    <div className="space-y-3">
                      <div className="text-sm text-on-surface-variant">{detail.identity.fileName || "未记录文件名"}</div>
                      <div className="flex flex-wrap gap-2">
                        <InlineBadge>{detail.identity.poolLabel}</InlineBadge>
                        <InlineBadge>{detail.identity.manualDecisionLabel}</InlineBadge>
                        <InlineBadge>{detail.identity.priorityLabel}</InlineBadge>
                        <InlineBadge tone={riskTone(detail.risk.level)}>{riskLabel(detail.risk.level)}</InlineBadge>
                        <InlineBadge>{detail.identity.parseStatusLabel}</InlineBadge>
                      </div>
                      <div className="rounded-[20px] bg-surface-container-low p-4 text-sm leading-7 text-on-surface-variant">
                        {detail.identity.reviewSummary || "暂无审核摘要。"}
                      </div>
                    </div>

                    <div className="grid gap-3 sm:grid-cols-2 xl:w-[360px]">
                      <ActionButton
                        onClick={() => claimMutation.mutate()}
                        disabled={actionDisabled || claimMutation.isPending || selfLocked}
                        tone="primary"
                      >
                        {selfLocked ? "已由我领取" : "领取候选人"}
                      </ActionButton>
                      <ActionButton
                        onClick={() => releaseMutation.mutate(false)}
                        disabled={actionDisabled || releaseMutation.isPending || (!selfLocked && !session.data?.is_admin)}
                      >
                        释放锁定
                      </ActionButton>
                      {session.data?.is_admin ? (
                        <ActionButton
                          onClick={() => releaseMutation.mutate(true)}
                          disabled={actionDisabled || releaseMutation.isPending || !lockState.isLockedEffective}
                        >
                          管理员强制释放
                        </ActionButton>
                      ) : null}
                    </div>
                  </div>

                  <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                    <InfoPill label="当前锁定" value={lockState.displayName || "未领取"} />
                    <InfoPill label="自动初筛" value={activeRow.autoScreeningResultLabel || "-"} />
                    <InfoPill label="人工结论" value={detail.identity.manualDecisionLabel || "-"} />
                    <InfoPill label="解析方式" value={detail.analysis.extractMethod || "-"} />
                  </div>
                </Panel>

                {requiresManualFirst ? (
                  <FeedbackBanner
                    tone="warning"
                    title="当前候选人建议人工优先复核"
                    detail="OCR / 解析质量偏弱，AI reviewer 会收敛为保守建议，不主动给出激进改分或推进结论。"
                  />
                ) : null}

                <Panel eyebrow="Analysis" title="解析质量与结构化画像">
                  <div className="grid gap-4 md:grid-cols-3">
                    <MetricCard label="OCR 置信度" value={formatConfidence(detail.analysis.ocrConfidence)} />
                    <MetricCard label="结构置信度" value={formatConfidence(detail.analysis.structureConfidence)} />
                    <MetricCard label="解析置信度" value={formatConfidence(detail.analysis.parseConfidence)} />
                  </div>

                  <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    <InfoPill label="分析模式" value={analysisModeLabel(detail.analysis.analysisMode)} />
                    <InfoPill label="提取质量" value={qualityLabel(detail.analysis.extractQuality)} />
                    <InfoPill label="提取方式" value={detail.analysis.extractMethod || "-"} />
                  </div>

                  <div className="mt-4 rounded-[20px] bg-surface-container-low p-4 text-sm leading-7 text-on-surface-variant">
                    {detail.analysis.extractMessage || "暂无额外解析说明。"}
                  </div>

                  <div className="mt-4 grid gap-4 xl:grid-cols-2">
                    {PROFILE_BLOCKS.map((block) => (
                      <ProfileCard key={block.key} label={block.label} value={stringify(profile[block.key]) || "暂无稳定信息"} />
                    ))}
                  </div>

                  <div className="mt-4 grid gap-4 xl:grid-cols-2">
                    <ClaimCard items={detail.analysis.claimCandidates} />
                    <GroundingSummaryCard summary={detail.analysis.groundingSummary} />
                  </div>

                  {detail.analysis.abstainReasons.length ? (
                    <div className="mt-4 flex flex-wrap gap-2">
                      {detail.analysis.abstainReasons.map((reason) => (
                        <InlineBadge key={`analysis-abstain-${reason}`} tone="warning">
                          {ABSTAIN_REASON_LABELS[reason] || reason}
                        </InlineBadge>
                      ))}
                    </div>
                  ) : null}
                </Panel>

                <Panel eyebrow="Evidence" title="证据链与反证链">
                  <div className="grid gap-4 xl:grid-cols-2">
                    <EvidenceCard title="关键证据摘要" items={detail.evidence.summarySnippets} emptyText="暂无关键证据摘要。" />
                    <EvidenceCard title="正向证据" items={detail.evidence.positiveEvidence} emptyText="暂无正向证据。" />
                    <EvidenceCard title="反证与冲突点" items={detail.evidence.counterEvidence} emptyText="暂无明显反证。" />
                    <EvidenceCard title="缺失信息" items={detail.evidence.missingInfoPoints} emptyText="暂无明显缺失点。" />
                  </div>

                  <div className="mt-4 grid gap-4 xl:grid-cols-2">
                    <EvidenceCard title="时间线风险" items={detail.evidence.timelineRisks} emptyText="暂无明显时间线风险。" />
                    <EvidenceCard title="证据追踪" items={detail.analysis.evidenceTrace} emptyText="暂无可展示的 evidence trace。" />
                  </div>
                </Panel>

                <Panel eyebrow="Risk" title="风险复核建议">
                  <div className="grid gap-3 md:grid-cols-2">
                    <InfoPill label="风险等级" value={riskLabel(detail.risk.level)} />
                    <InfoPill label="风险摘要" value={detail.risk.summary || "暂无风险摘要"} />
                  </div>

                  <div className="mt-4 grid gap-4 xl:grid-cols-2">
                    <SimpleList title="风险点" items={detail.risk.points} emptyText="暂无风险点。" />
                    <SimpleList title="筛选原因" items={detail.risk.screeningReasons} emptyText="暂无筛选原因。" />
                  </div>
                </Panel>

                <Panel eyebrow="AI Reviewer" title="AI 建议与采纳状态">
                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                    <InfoPill label="状态" value={detail.aiReview.status || "not_generated"} />
                    <InfoPill label="来源" value={detail.aiReview.source || "-"} />
                    <InfoPill label="模型" value={detail.aiReview.model || "-"} />
                    <InfoPill label="生成时间" value={detail.aiReview.generatedAt || "-"} />
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    {detail.aiReview.recommendedActionDetail?.supportStatus ? (
                      <SupportBadge status={detail.aiReview.recommendedActionDetail.supportStatus} />
                    ) : null}
                    {detail.aiReview.recommendedActionDetail?.needsManualCheck ? (
                      <InlineBadge tone="warning">建议人工复核</InlineBadge>
                    ) : null}
                    {detail.evidence.counterEvidence.length ? <InlineBadge tone="danger">存在反证</InlineBadge> : null}
                    {detail.evidence.missingInfoPoints.length ? <InlineBadge tone="warning">证据不足</InlineBadge> : null}
                    {detail.aiReview.appliedActions.length ? <InlineBadge tone="success">已采纳部分建议</InlineBadge> : null}
                  </div>

                  <div className="mt-4 flex flex-wrap gap-3">
                    <ActionButton
                      onClick={() => aiMutation.mutate()}
                      disabled={actionDisabled || aiMutation.isPending || lockedByOther}
                      tone="primary"
                    >
                      {aiMutation.isPending ? "生成中..." : "生成 / 刷新 AI 建议"}
                    </ActionButton>
                    <ActionButton
                      onClick={() => applyAiMutation.mutate("scores")}
                      disabled={actionDisabled || applyAiMutation.isPending || lockedByOther}
                    >
                      应用证据 + 评分
                    </ActionButton>
                    <ActionButton
                      onClick={() => applyAiMutation.mutate("all")}
                      disabled={actionDisabled || applyAiMutation.isPending || lockedByOther}
                    >
                      应用全部建议
                    </ActionButton>
                    <ActionButton
                      onClick={() => revertAiMutation.mutate()}
                      disabled={actionDisabled || revertAiMutation.isPending || lockedByOther}
                    >
                      回退 AI 应用
                    </ActionButton>
                  </div>

                  <div className="mt-4 rounded-[20px] bg-surface-container-low p-4">
                    <div className="flex flex-wrap gap-2">
                      <InlineBadge tone="primary">
                        {RECOMMENDED_ACTION_LABELS[detail.aiReview.recommendedAction] || detail.aiReview.recommendedAction || "不主动改动"}
                      </InlineBadge>
                      {detail.aiReview.recommendedActionDetail?.supportStatus ? (
                        <SupportBadge status={detail.aiReview.recommendedActionDetail.supportStatus} />
                      ) : null}
                      {detail.aiReview.recommendedActionDetail?.groundedConfidence > 0 ? (
                        <InlineBadge>{`置信 ${Math.round(detail.aiReview.recommendedActionDetail.groundedConfidence * 100)}%`}</InlineBadge>
                      ) : null}
                    </div>
                    <div className="mt-3 text-sm leading-7 text-on-surface-variant">
                      {detail.aiReview.reviewSummary || "暂无 AI 摘要。"}
                    </div>
                    {detail.aiReview.recommendedActionDetail?.reason ? (
                      <div className="mt-3 text-sm leading-7 text-on-surface">
                        {detail.aiReview.recommendedActionDetail.reason}
                      </div>
                    ) : null}
                    {detail.aiReview.error ? <div className="mt-3 text-sm text-error">错误：{detail.aiReview.error}</div> : null}
                    {detail.aiReview.abstainReasons.length ? (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {detail.aiReview.abstainReasons.map((reason) => (
                          <InlineBadge key={`ai-abstain-${reason}`} tone="warning">
                            {ABSTAIN_REASON_LABELS[reason] || reason}
                          </InlineBadge>
                        ))}
                      </div>
                    ) : null}
                  </div>

                  <div className="mt-4 grid gap-4 xl:grid-cols-2">
                    <ScoreAdjustmentCard items={detail.aiReview.scoreAdjustments} />
                    <div className="rounded-[20px] bg-surface-container-low p-4">
                      <div className="font-semibold text-on-surface">采纳状态</div>
                      <div className="mt-3 space-y-3 text-sm text-on-surface-variant">
                        <InfoPill label="已采纳动作" value={detail.aiReview.appliedActions.join(" / ") || "暂无"} />
                        <InfoPill
                          label="风险建议"
                          value={
                            detail.aiReview.riskAdjustment?.reason ||
                            detail.aiReview.riskAdjustment?.suggestedRiskLevel ||
                            "暂无"
                          }
                        />
                        {detail.aiReview.riskAdjustment?.supportStatus ? (
                          <SupportBadge status={detail.aiReview.riskAdjustment.supportStatus} />
                        ) : null}
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-4 xl:grid-cols-2">
                    <SimpleList title="建议追问问题" items={detail.aiReview.interviewQuestions} emptyText="暂无建议追问问题。" />
                    <SimpleList title="建议关注点" items={detail.aiReview.focusPoints} emptyText="暂无建议关注点。" />
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
                        <select value={manualPriority} onChange={(event) => setManualPriority(event.target.value)} className="input-shell">
                          {PRIORITY_OPTIONS.map((item) => (
                            <option key={item.value} value={item.value}>
                              {item.label}
                            </option>
                          ))}
                        </select>
                      </Field>
                      <ActionButton
                        onClick={() => priorityMutation.mutate()}
                        disabled={actionDisabled || priorityMutation.isPending || lockedByOther}
                        className="self-end"
                      >
                        保存优先级
                      </ActionButton>
                    </div>

                    <ActionButton
                      onClick={() => noteMutation.mutate()}
                      disabled={actionDisabled || noteMutation.isPending || lockedByOther}
                    >
                      保存人工备注
                    </ActionButton>

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

function analysisModeLabel(mode) {
  const labels = {
    normal: "标准分析",
    weak_text: "弱文本模式",
    manual_first: "人工优先复核"
  };
  return labels[mode] || mode || "-";
}

function qualityLabel(value) {
  if (!value) {
    return "-";
  }
  if (String(value).toLowerCase() === "ok") {
    return "正常";
  }
  if (String(value).toLowerCase() === "weak") {
    return "较弱";
  }
  return String(value);
}

function riskLabel(level) {
  const labels = {
    low: "低风险",
    medium: "中风险",
    high: "高风险",
    unknown: "未知风险"
  };
  return labels[level] || level || "未知风险";
}

function riskTone(level) {
  if (level === "high") {
    return "danger";
  }
  if (level === "medium") {
    return "warning";
  }
  if (level === "low") {
    return "success";
  }
  return "default";
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

function HeroMetricCard({ label, value, tone }) {
  const toneClass =
    tone === "emerald"
      ? "bg-emerald-50 text-emerald-700"
      : tone === "amber"
        ? "bg-amber-50 text-amber-700"
        : tone === "rose"
          ? "bg-rose-50 text-rose-700"
          : "bg-surface-container-low text-on-surface";
  return (
    <div className={`rounded-[22px] p-4 ${toneClass}`}>
      <div className="text-xs uppercase tracking-[0.12em] opacity-75">{label}</div>
      <div className="mt-3 font-headline text-3xl font-extrabold">{value}</div>
    </div>
  );
}

function QueueStat({ label, value }) {
  return (
    <div className="rounded-[18px] bg-surface-container-low p-4">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{label}</div>
      <div className="mt-3 text-sm font-semibold text-on-surface">{value}</div>
    </div>
  );
}

function QueueCard({ candidate, active, checked, onToggleChecked, onSelect }) {
  return (
    <div
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
          onChange={onToggleChecked}
          className="mt-1 h-4 w-4 rounded border-white/40"
        />
        <button onClick={onSelect} className="min-w-0 flex-1 text-left">
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
            <MiniBadge active={active}>{riskLabel(candidate.riskLevel)}</MiniBadge>
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
}

function InfoPill({ label, value }) {
  return (
    <div className="rounded-[18px] bg-surface-container-low px-4 py-3 text-sm">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{label}</div>
      <div className="mt-2 text-on-surface">{String(value ?? "-")}</div>
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
      <div className="mt-3 text-sm leading-7 text-on-surface whitespace-pre-wrap">{String(value || "-")}</div>
    </div>
  );
}

function ClaimCard({ items }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="font-semibold text-on-surface">可主张结论</div>
      {!items?.length ? (
        <div className="mt-3 text-sm text-on-surface-variant">暂无可展示的 claim candidates。</div>
      ) : (
        <div className="mt-3 space-y-3">
          {items.map((item, index) => (
            <div key={`claim-${index}`} className="rounded-[16px] bg-white/80 p-3">
              <div className="text-sm font-semibold text-on-surface">{item.claim || "未命名主张"}</div>
              {item.supporting_evidence_ids?.length ? (
                <div className="mt-2 text-xs text-on-surface-variant">
                  支持证据：{item.supporting_evidence_ids.join(" / ")}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function GroundingSummaryCard({ summary }) {
  const items = [
    { label: "JD anchors", value: summary?.jd_semantic_anchors?.length || 0 },
    { label: "正向证据", value: summary?.positive_evidence?.length || 0 },
    { label: "反证", value: summary?.counter_evidence?.length || 0 },
    { label: "缺失点", value: summary?.missing_evidence?.length || 0 },
    { label: "历史案例", value: summary?.historical_case_grounding?.length || 0 },
    { label: "风险案例", value: summary?.risk_case_grounding?.length || 0 }
  ];

  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="font-semibold text-on-surface">Grounding 摘要</div>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {items.map((item) => (
          <InfoPill key={item.label} label={item.label} value={item.value} />
        ))}
      </div>
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
                {item.label ? <InlineBadge>{item.label}</InlineBadge> : null}
                {item.tags?.map((tag) => (
                  <InlineBadge key={`${title}-${index}-${tag}`}>{tag}</InlineBadge>
                ))}
                {item.supportStatus ? <SupportBadge status={item.supportStatus} /> : null}
                {item.needsManualCheck ? <InlineBadge tone="warning">建议人工复核</InlineBadge> : null}
                {item.groundedConfidence > 0 ? (
                  <InlineBadge>{`置信 ${Math.round(item.groundedConfidence * 100)}%`}</InlineBadge>
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

function ScoreAdjustmentCard({ items }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4">
      <div className="font-semibold text-on-surface">维度改分建议</div>
      {!items?.length ? (
        <div className="mt-3 text-sm text-on-surface-variant">暂无改分建议。</div>
      ) : (
        <div className="mt-3 space-y-3">
          {items.map((item, index) => (
            <div key={`score-adjustment-${index}`} className="rounded-[16px] bg-white/80 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <InlineBadge tone="primary">{item.dimension || "未命名维度"}</InlineBadge>
                <InlineBadge>{`建议 ${signedDelta(item.suggestedDelta)}`}</InlineBadge>
                <InlineBadge>{`上限 ${signedDelta(item.maxDelta)}`}</InlineBadge>
                {item.supportStatus ? <SupportBadge status={item.supportStatus} /> : null}
                {item.needsManualCheck ? <InlineBadge tone="warning">建议人工复核</InlineBadge> : null}
              </div>
              <div className="mt-2 text-sm text-on-surface-variant">
                当前分数：{item.currentScore ?? "-"} · 支持证据 {item.supportingEvidenceIds?.length || 0} 条 · 反证 {item.opposingEvidenceIds?.length || 0} 条
              </div>
              <div className="mt-2 text-sm leading-7 text-on-surface">{item.reason || "暂无解释"}</div>
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

function MiniBadge({ children, active = false }) {
  return (
    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${active ? "bg-white/15 text-white" : "bg-surface-container-low text-on-surface-variant"}`}>
      {children}
    </span>
  );
}

function InlineBadge({ children, tone = "default" }) {
  const toneClass =
    tone === "primary"
      ? "bg-primary/10 text-primary"
      : tone === "success"
        ? "bg-emerald-50 text-emerald-700"
        : tone === "warning"
          ? "bg-amber-50 text-amber-800"
          : tone === "danger"
            ? "bg-rose-50 text-rose-700"
            : "bg-surface-container-low text-on-surface-variant";
  return <span className={`rounded-full px-3 py-1 text-xs font-semibold ${toneClass}`}>{children}</span>;
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
  return <span className={`rounded-full px-3 py-1 text-xs font-semibold ${toneClass}`}>{SUPPORT_STATUS_LABELS[status] || status || "证据状态未知"}</span>;
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

function ActionButton({ children, className = "", tone = "default", ...props }) {
  const toneClass =
    tone === "primary"
      ? "bg-primary text-white"
      : "bg-surface-container-high text-primary";
  return (
    <button
      {...props}
      className={`rounded-2xl px-4 py-3 text-sm font-semibold disabled:cursor-not-allowed disabled:opacity-60 ${toneClass} ${className}`.trim()}
    >
      {children}
    </button>
  );
}
