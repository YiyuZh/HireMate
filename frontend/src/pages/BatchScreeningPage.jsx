import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";

import AppShell from "../components/AppShell";
import { useSession } from "../components/RequireAuth";
import { api } from "../lib/api";
import { normalizeJobDetail, normalizeJobs, normalizePrecheckItems } from "../lib/viewModels";

const MODEL_PRESETS = {
  openai: ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"],
  openai_compatible: ["gpt-4o-mini", "deepseek-chat"],
  deepseek: ["deepseek-chat", "deepseek-reasoner"],
  azure_openai: ["gpt-4o-mini", "gpt-4.1-mini"],
  anthropic: ["claude-3-5-sonnet-latest"],
  mock: ["mock-reviewer"]
};

const initialRuntime = {
  enable_ai_reviewer: false,
  provider: "openai",
  model: "gpt-4o-mini",
  api_base: "",
  api_key_mode: "direct_input",
  api_key_env_name: "OPENAI_API_KEY",
  api_key_value: "",
  auto_generate_for_new_batch: false
};

export default function BatchScreeningPage() {
  const session = useSession();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const preferredJob = searchParams.get("jd_title") || "";
  const [selectedJob, setSelectedJob] = useState(preferredJob);
  const [runtime, setRuntime] = useState(initialRuntime);
  const [files, setFiles] = useState([]);
  const [banner, setBanner] = useState(null);
  const [connectionStage, setConnectionStage] = useState("idle");

  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: async () => normalizeJobs(await api.get("/api/jobs"))
  });

  const detailQuery = useQuery({
    queryKey: ["job", selectedJob],
    queryFn: async () => normalizeJobDetail(await api.get(`/api/jobs/${encodeURIComponent(selectedJob)}`)),
    enabled: Boolean(selectedJob)
  });

  const precheckMutation = useMutation({
    mutationFn: async () => {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      const payload = await api.post("/api/screening/precheck", form);
      return normalizePrecheckItems(payload);
    },
    onError: (error) => {
      setBanner({
        tone: "error",
        title: "提取预检失败",
        detail: error.message || "请检查文件内容后重试。"
      });
    }
  });

  const connectionMutation = useMutation({
    mutationFn: () =>
      api.post("/api/screening/ai/test-connection", {
        runtime_config: runtime,
        purpose: "batch_runtime"
      }),
    onSuccess: (payload) => {
      setBanner({
        tone: payload?.success ? "success" : "warning",
        title: payload?.success ? "AI 连接测试通过" : "AI 连接测试未通过",
        detail: payload?.reason || payload?.message || "请检查当前 API 配置。"
      });
    },
    onError: (error) => {
      setBanner({
        tone: "error",
        title: "AI 连接测试失败",
        detail: error.message || "请检查当前 API 配置。"
      });
    }
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const activeJob = detailQuery.data;
      const form = new FormData();
      form.append("jd_title", selectedJob);
      form.append("jd_text", activeJob?.jdText || "");
      form.append("runtime_config_json", JSON.stringify(runtime));
      form.append("force_allow_weak", "false");
      files.forEach((file) => form.append("files", file));
      return api.post("/api/batches", form);
    },
    onSuccess: (payload) => {
      const batchId = payload?.batch_id || payload?.batchId;
      setBanner({
        tone: "success",
        title: "批量初筛已创建",
        detail: batchId ? `批次 ${batchId} 已创建，正在进入候选人工作台。` : "正在进入候选人工作台。"
      });
      navigate(`/workbench?batch_id=${encodeURIComponent(batchId || "")}&jd_title=${encodeURIComponent(selectedJob)}`);
    },
    onError: (error) => {
      setBanner({
        tone: "error",
        title: "批量初筛创建失败",
        detail: error.message || "请先处理弱质量文件或调整本批次配置。"
      });
    }
  });

  useEffect(() => {
    const jobs = jobsQuery.data || [];
    if (!selectedJob && jobs.length) {
      setSelectedJob(preferredJob || jobs[0].title);
    }
  }, [jobsQuery.data, preferredJob, selectedJob]);

  useEffect(() => {
    if (!detailQuery.data) {
      return;
    }
    const defaults = detailQuery.data.aiDefaults || {};
    setRuntime((prev) => ({
      ...prev,
      enable_ai_reviewer: Boolean(defaults.enableAiReviewer),
      provider: defaults.provider || prev.provider || "openai",
      model: defaults.model || MODEL_PRESETS[defaults.provider || prev.provider || "openai"]?.[0] || prev.model,
      api_base: defaults.apiBase || defaultApiBase(defaults.provider || prev.provider || "openai")
    }));
  }, [detailQuery.data?.title]);

  useEffect(() => {
    if (!connectionMutation.isPending) {
      setConnectionStage("idle");
      return undefined;
    }
    setConnectionStage("validating");
    const timer = setTimeout(() => setConnectionStage("network"), 700);
    return () => clearTimeout(timer);
  }, [connectionMutation.isPending]);

  const currentJob = useMemo(
    () => jobsQuery.data?.find((item) => item.title === selectedJob) || null,
    [jobsQuery.data, selectedJob]
  );

  const modelPresets = MODEL_PRESETS[runtime.provider] || [];
  const precheckItems = precheckMutation.data || [];
  const connectionResult = connectionMutation.data || null;
  const currentBatchHint = currentJob?.latestBatch || null;

  function updateRuntime(key, value) {
    setRuntime((prev) => ({ ...prev, [key]: value }));
  }

  function applyModelPreset(value) {
    if (!value) {
      return;
    }
    setRuntime((prev) => ({ ...prev, model: value }));
  }

  const connectionTone = connectionMutation.isPending
    ? "warning"
    : connectionResult?.success
      ? "success"
      : connectionResult
        ? "error"
        : "neutral";

  return (
    <AppShell
      user={session.data}
      eyebrow="Batch Screening"
      title="批量初筛"
      subtitle="在进入候选人工作台前，先完成岗位确认、AI reviewer 运行配置、文件预检和批次创建。"
    >
      <div className="grid gap-6 xl:grid-cols-[1.15fr_0.85fr]">
        <section className="space-y-6 rounded-[24px] bg-surface-container-lowest p-8 shadow-ambient">
          {banner ? <Banner {...banner} /> : null}

          <SectionHeader
            eyebrow="Batch Setup"
            title="本批次运行配置"
            description="JD 页面只保留默认配置；这里才是本批次真正生效的 AI reviewer 设置。创建批次后，工作台会继承这一组运行参数。"
          />

          <div className="grid gap-5 md:grid-cols-2">
            <Field label="目标岗位">
              <select value={selectedJob} onChange={(event) => setSelectedJob(event.target.value)} className="input-shell">
                <option value="">请选择岗位</option>
                {(jobsQuery.data || []).map((job) => (
                  <option key={job.title} value={job.title}>
                    {job.title}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="AI Provider">
              <select
                value={runtime.provider}
                onChange={(event) => {
                  const provider = event.target.value;
                  setRuntime((prev) => ({
                    ...prev,
                    provider,
                    model: MODEL_PRESETS[provider]?.[0] || prev.model,
                    api_base: defaultApiBase(provider)
                  }));
                }}
                className="input-shell"
              >
                <option value="openai">openai</option>
                <option value="openai_compatible">openai_compatible</option>
                <option value="deepseek">deepseek</option>
                <option value="azure_openai">azure_openai</option>
                <option value="anthropic">anthropic</option>
                <option value="mock">mock</option>
              </select>
            </Field>

            <Field label="模型预设">
              <select value="" onChange={(event) => applyModelPreset(event.target.value)} className="input-shell">
                <option value="">选择预设模型</option>
                {modelPresets.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="模型名称">
              <input
                value={runtime.model}
                onChange={(event) => updateRuntime("model", event.target.value)}
                className="input-shell"
                placeholder="支持自定义模型名称"
              />
            </Field>

            <Field label="API Base">
              <input
                value={runtime.api_base}
                onChange={(event) => updateRuntime("api_base", event.target.value)}
                className="input-shell"
                placeholder="例如 https://api.deepseek.com/v1"
              />
            </Field>

            <Field label="API Key 模式">
              <select
                value={runtime.api_key_mode}
                onChange={(event) => updateRuntime("api_key_mode", event.target.value)}
                className="input-shell"
              >
                <option value="direct_input">直接输入 API Key</option>
                <option value="env_name">使用环境变量名</option>
              </select>
            </Field>
          </div>

          {runtime.api_key_mode === "direct_input" ? (
            <Field label="Direct API Key">
              <input
                type="password"
                value={runtime.api_key_value}
                onChange={(event) => updateRuntime("api_key_value", event.target.value)}
                className="input-shell"
                placeholder="只用于当前批次联调，不会写回明文"
              />
            </Field>
          ) : (
            <Field label="环境变量名">
              <input
                value={runtime.api_key_env_name}
                onChange={(event) => updateRuntime("api_key_env_name", event.target.value)}
                className="input-shell"
                placeholder="例如 OPENAI_API_KEY / DEEPSEEK_API_KEY"
              />
            </Field>
          )}

          <div className="grid gap-3 md:grid-cols-2">
            <ToggleCard
              label="启用 AI reviewer"
              description="AI reviewer 仍然只给建议，不会直接替代人工最终结论。"
              checked={runtime.enable_ai_reviewer}
              onChange={(checked) => updateRuntime("enable_ai_reviewer", checked)}
            />
            <ToggleCard
              label="新批次自动生成 AI 建议"
              description="创建批次后立即尝试生成建议；失败会安全退化，不阻塞主流程。"
              checked={runtime.auto_generate_for_new_batch}
              onChange={(checked) => updateRuntime("auto_generate_for_new_batch", checked)}
            />
          </div>

          <div className="rounded-[24px] bg-surface-container-low p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">Resume Intake</div>
                <div className="mt-2 text-sm text-on-surface-variant">
                  先做提取预检，再决定是否创建批次，避免弱质量文件直接冲进稳定评估。
                </div>
              </div>
              <div className="rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">
                已选 {files.length} 份文件
              </div>
            </div>

            <input
              type="file"
              multiple
              onChange={(event) => setFiles(Array.from(event.target.files || []))}
              className="mt-4 block w-full text-sm"
            />

            <div className="mt-5 flex flex-wrap gap-3">
              <button
                onClick={() => precheckMutation.mutate()}
                disabled={!files.length || precheckMutation.isPending}
                className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                {precheckMutation.isPending ? "正在预检..." : "运行提取预检"}
              </button>
              <button
                onClick={() => connectionMutation.mutate()}
                disabled={connectionMutation.isPending}
                className="rounded-2xl bg-surface-container-high px-4 py-3 text-sm font-semibold text-primary disabled:cursor-not-allowed disabled:opacity-60"
              >
                {connectionMutation.isPending
                  ? connectionStage === "network"
                    ? "正在连接上游..."
                    : "正在校验配置..."
                  : "测试 AI 连接"}
              </button>
              <button
                onClick={() => createMutation.mutate()}
                disabled={!files.length || !selectedJob || createMutation.isPending}
                className="rounded-2xl bg-primary px-4 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
              >
                {createMutation.isPending ? "正在创建批次..." : "创建批量初筛"}
              </button>
            </div>
          </div>
        </section>

        <section className="space-y-6">
          <Panel title="当前岗位上下文">
            {detailQuery.isLoading ? (
              <EmptyState title="正在读取岗位详情" description="请稍候，系统正在同步当前岗位的 JD 与默认 AI 配置。" />
            ) : (
              <div className="space-y-4 text-sm leading-7 text-on-surface-variant">
                <InfoRow label="岗位名称" value={detailQuery.data?.title || selectedJob || "未选择岗位"} />
                <InfoRow label="开放人数" value={detailQuery.data?.openings || 0} />
                <InfoRow label="默认 Provider" value={detailQuery.data?.aiDefaults?.provider || "openai"} />
                <InfoRow label="默认 Model" value={detailQuery.data?.aiDefaults?.model || "-"} />
                <div className="rounded-[18px] bg-surface-container-low p-4">
                  {(detailQuery.data?.jdText || currentJob?.jdTextPreview || "选择岗位后预览 JD 文本。").slice(0, 420)}
                </div>
              </div>
            )}
          </Panel>

          <Panel title="预检结果">
            {!precheckItems.length ? (
              <EmptyState
                title="还没有预检结果"
                description="上传简历后点击“运行提取预检”，先确认哪些文件可以稳定进入批量初筛。"
              />
            ) : (
              <div className="space-y-3">
                {precheckItems.map((item) => (
                  <div key={item.fileName} className="rounded-[18px] bg-surface-container-low p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="font-semibold text-on-surface">{item.fileName}</div>
                        <div className="mt-1 text-xs text-on-surface-variant">
                          {item.method} · {item.qualityLabel} · {item.parseStatusLabel}
                        </div>
                      </div>
                      <StatusPill tone={item.canEnterBatchScreening ? "success" : "warning"}>
                        {item.canEnterBatchScreening ? "可进入批次" : "建议人工处理"}
                      </StatusPill>
                    </div>
                    <div className="mt-3 text-sm text-on-surface-variant">{item.message || "无额外说明"}</div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="AI 连接测试">
            {connectionMutation.isPending ? (
              <ConnectionProbeState stage={connectionStage} />
            ) : !connectionResult ? (
              <EmptyState
                title="还没有连接测试结果"
                description="点击“测试 AI 连接”后，这里会展示本地校验结果、远程探测耗时以及失败原因。"
              />
            ) : (
              <div className="space-y-4">
                <StatusPill tone={connectionTone}>
                  {connectionResult.success ? "连接成功" : connectionCategoryLabel(connectionResult.category)}
                </StatusPill>

                <div className="grid gap-3 md:grid-cols-2">
                  <InfoRow label="provider" value={connectionResult.provider || runtime.provider} />
                  <InfoRow label="model" value={connectionResult.model || runtime.model} />
                  <InfoRow label="api_base" value={connectionResult.api_base || runtime.api_base || "-"} />
                  <InfoRow label="阶段" value={connectionPhaseLabel(connectionResult.phase)} />
                  <InfoRow label="来源" value={connectionResult.source || "-"} />
                  <InfoRow
                    label="key 状态"
                    value={connectionResult.api_key_present ? "已检测到" : "未检测到"}
                  />
                  <InfoRow label="本地校验耗时" value={formatMs(connectionResult.validation_ms)} />
                  <InfoRow label="远程探测耗时" value={formatMs(connectionResult.network_ms)} />
                  <InfoRow label="总耗时" value={formatMs(connectionResult.latency_ms)} />
                  <InfoRow label="模式" value={connectionResult.api_key_mode_label || connectionResult.api_key_mode || "-"} />
                </div>

                <div className="rounded-[18px] bg-surface-container-low p-4 text-sm text-on-surface-variant">
                  <div className="font-semibold text-on-surface">结果说明</div>
                  <div className="mt-2 leading-7">
                    {connectionResult.reason || connectionResult.message || "未返回额外说明"}
                  </div>
                </div>
              </div>
            )}
          </Panel>

          <Panel title="最近批次">
            {!currentBatchHint?.batchId ? (
              <EmptyState title="还没有历史批次" description="当前岗位还没有最近批次记录。" />
            ) : (
              <div className="space-y-3 text-sm text-on-surface-variant">
                <InfoRow label="batch_id" value={currentBatchHint.batchId} />
                <InfoRow label="created_at" value={currentBatchHint.createdAt || "-"} />
                <InfoRow label="总文件数" value={currentBatchHint.totalResumes} />
                <InfoRow
                  label="通过 / 待复核 / 淘汰"
                  value={`${currentBatchHint.passCount} / ${currentBatchHint.reviewCount} / ${currentBatchHint.rejectCount}`}
                />
              </div>
            )}
          </Panel>
        </section>
      </div>
    </AppShell>
  );
}

function defaultApiBase(provider) {
  if (provider === "deepseek") {
    return "https://api.deepseek.com/v1";
  }
  return "";
}

function formatMs(value) {
  const num = Number(value || 0);
  return num > 0 ? `${num} ms` : "-";
}

function connectionPhaseLabel(phase) {
  if (phase === "network_probe") {
    return "远程探测";
  }
  return "本地校验";
}

function connectionCategoryLabel(category) {
  const labels = {
    success: "连接成功",
    config_error: "配置错误",
    network_timeout: "网络超时",
    network_error: "网络异常",
    upstream_rejected: "上游拒绝",
    auth_error: "鉴权失败",
    unsupported_provider: "Provider 未实现",
    mock: "Mock 模式",
    unknown: "连接失败"
  };
  return labels[category] || category || "连接失败";
}

function Banner({ tone, title, detail }) {
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

function SectionHeader({ eyebrow, title, description }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{eyebrow}</div>
      <h2 className="mt-2 font-headline text-3xl font-extrabold text-on-surface">{title}</h2>
      <p className="mt-2 text-sm leading-7 text-on-surface-variant">{description}</p>
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

function ToggleCard({ label, description, checked, onChange }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`rounded-[20px] p-4 text-left transition ${
        checked ? "bg-primary text-white" : "bg-surface-container-low text-on-surface"
      }`}
    >
      <div className="text-xs uppercase tracking-[0.12em] opacity-70">Toggle</div>
      <div className="mt-2 font-semibold">{label}</div>
      <div className="mt-2 text-sm opacity-80">{description}</div>
      <div className="mt-3 text-xs font-semibold">{checked ? "已启用" : "未启用"}</div>
    </button>
  );
}

function Panel({ title, children }) {
  return (
    <div className="rounded-[24px] bg-surface-container-lowest p-6 shadow-ambient">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{title}</div>
      <div className="mt-4">{children}</div>
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
    <div className="flex items-start justify-between gap-4 rounded-[16px] bg-surface-container-low px-4 py-3">
      <span className="text-xs uppercase tracking-[0.08em] text-on-surface-variant">{label}</span>
      <span className="text-right text-on-surface">{String(value ?? "-")}</span>
    </div>
  );
}

function StatusPill({ tone, children }) {
  const toneClass =
    tone === "success"
      ? "bg-emerald-50 text-emerald-700"
      : tone === "error"
        ? "bg-rose-50 text-rose-700"
        : tone === "warning"
          ? "bg-amber-50 text-amber-700"
          : "bg-surface-container-low text-on-surface";
  return <span className={`rounded-full px-3 py-1 text-xs font-semibold ${toneClass}`}>{children}</span>;
}

function ConnectionProbeState({ stage }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-5 text-sm text-on-surface-variant">
      <div className="font-semibold text-on-surface">
        {stage === "network" ? "正在连接上游服务" : "正在校验本地配置"}
      </div>
      <div className="mt-2 leading-7">
        {stage === "network"
          ? "本地校验已通过，正在发起轻量远程探测。若此阶段较慢，通常是 api_base、网络、DNS、TLS 或上游响应引起。"
          : "系统会先检查 provider、api_base 与 API key 是否可用；如果本地配置有问题，会在这一阶段直接快速返回。"}
      </div>
    </div>
  );
}
