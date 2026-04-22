import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import AppShell from "../components/AppShell";
import { useSession } from "../components/RequireAuth";
import { api } from "../lib/api";
import { normalizeJobDetail } from "../lib/viewModels";

const BASE_WEIGHT_KEYS = ["教育背景匹配度", "相关经历匹配度", "技能匹配度", "表达完整度"];

export default function JobDetailPage() {
  const { jdTitle } = useParams();
  const decodedTitle = decodeURIComponent(jdTitle || "");
  const session = useSession();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState(null);
  const [banner, setBanner] = useState(null);

  const detailQuery = useQuery({
    queryKey: ["job", decodedTitle],
    queryFn: async () => normalizeJobDetail(await api.get(`/api/jobs/${encodeURIComponent(decodedTitle)}`))
  });

  useEffect(() => {
    if (!detailQuery.data) {
      return;
    }
    setDraft({
      jdText: detailQuery.data.jdText || "",
      openings: detailQuery.data.openings || 0,
      scoringConfigText: JSON.stringify(detailQuery.data.scoringConfig || {}, null, 2)
    });
  }, [detailQuery.data?.title]);

  const saveMutation = useMutation({
    mutationFn: (payload) => api.put(`/api/jobs/${encodeURIComponent(decodedTitle)}`, payload),
    onSuccess: async () => {
      setBanner({
        tone: "success",
        title: "岗位配置已保存",
        detail: "岗位默认配置已经刷新，新的批量初筛会继续继承这份默认配置。"
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
        queryClient.invalidateQueries({ queryKey: ["job", decodedTitle] })
      ]);
    },
    onError: (error) => {
      setBanner({
        tone: "error",
        title: "岗位配置保存失败",
        detail: error.message || "请检查当前 JD 文本和 JSON 配置。"
      });
    }
  });

  const detail = detailQuery.data;
  const parsedConfig = safeParseJson(draft?.scoringConfigText);
  const weights = parsedConfig?.weights || {};
  const aiDefaults = detail?.aiDefaults || {};

  return (
    <AppShell
      user={session.data}
      eyebrow="Job Config"
      title={decodedTitle || "岗位详情"}
      subtitle="岗位配置继续作为默认源头，批量初筛只继承默认值，不再承载长期默认配置本身。"
    >
      <div className="grid gap-6 xl:grid-cols-[1.08fr_0.92fr]">
        <section className="space-y-6 rounded-[24px] bg-surface-container-lowest p-8 shadow-ambient">
          {banner ? <Banner {...banner} /> : null}

          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">Core Setup</div>
              <h2 className="mt-2 font-headline text-3xl font-extrabold text-on-surface">岗位默认配置</h2>
              <p className="mt-2 text-sm leading-7 text-on-surface-variant">
                这里保留岗位级默认配置，包括 JD 文本、基础权重和 AI reviewer 默认值。
              </p>
            </div>
            <button
              onClick={() =>
                saveMutation.mutate({
                  jd_text: draft?.jdText || "",
                  openings: Number(draft?.openings || 0),
                  scoring_config: parsedConfig
                })
              }
              disabled={!draft || saveMutation.isPending}
              className="rounded-2xl bg-primary px-5 py-3 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saveMutation.isPending ? "正在保存..." : "保存修改"}
            </button>
          </div>

          {detailQuery.isLoading || !draft ? (
            <EmptyState title="正在读取岗位配置" description="请稍候，系统正在同步当前岗位详情。" />
          ) : (
            <div className="space-y-5">
              <div className="grid gap-5 md:grid-cols-2">
                <Field label="岗位名称">
                  <input value={decodedTitle} readOnly className="input-shell opacity-70" />
                </Field>
                <Field label="开放人数">
                  <input
                    type="number"
                    value={draft.openings}
                    onChange={(event) =>
                      setDraft((prev) => ({
                        ...(prev || {}),
                        openings: event.target.value
                      }))
                    }
                    className="input-shell"
                  />
                </Field>
              </div>

              <Field label="岗位 JD 文本">
                <textarea
                  rows={18}
                  value={draft.jdText}
                  onChange={(event) =>
                    setDraft((prev) => ({
                      ...(prev || {}),
                      jdText: event.target.value
                    }))
                  }
                  className="w-full rounded-[20px] bg-surface-container-low px-4 py-4 text-sm leading-7 outline-none focus:bg-white focus:ring-2 focus:ring-primary"
                />
              </Field>

              <Field label="高级配置 JSON">
                <textarea
                  rows={14}
                  value={draft.scoringConfigText}
                  onChange={(event) =>
                    setDraft((prev) => ({
                      ...(prev || {}),
                      scoringConfigText: event.target.value
                    }))
                  }
                  className="w-full rounded-[20px] bg-surface-container-low px-4 py-4 font-mono text-xs leading-6 outline-none focus:bg-white focus:ring-2 focus:ring-primary"
                />
              </Field>
            </div>
          )}
        </section>

        <section className="space-y-6">
          <Panel title="基础权重摘要">
            <div className="space-y-3">
              {BASE_WEIGHT_KEYS.map((key) => (
                <InfoRow key={key} label={key} value={weights?.[key] ?? "-"} />
              ))}
            </div>
          </Panel>

          <Panel title="AI reviewer 默认值">
            <div className="space-y-3 text-sm text-on-surface-variant">
              <InfoRow label="启用默认值" value={aiDefaults.enableAiReviewer ? "是" : "否"} />
              <InfoRow label="provider" value={aiDefaults.provider || "openai"} />
              <InfoRow label="model" value={aiDefaults.model || "-"} />
              <InfoRow label="api_base" value={aiDefaults.apiBase || "-"} />
            </div>
          </Panel>

          <Panel title="最近批次">
            {!detail?.batches?.length ? (
              <EmptyState title="还没有历史批次" description="从这里保存岗位后，可以直接进入批量初筛创建第一个批次。" />
            ) : (
              <div className="space-y-4">
                {detail.batches.slice(0, 6).map((batch) => (
                  <div key={batch.batchId} className="rounded-[20px] bg-surface-container-low p-4">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <div className="font-semibold text-on-surface">{batch.batchId}</div>
                        <div className="mt-1 text-xs text-on-surface-variant">{batch.createdAt || "-"}</div>
                      </div>
                      <Link
                        to={`/workbench?batch_id=${encodeURIComponent(batch.batchId)}&jd_title=${encodeURIComponent(decodedTitle)}`}
                        className="rounded-xl bg-primary px-3 py-2 text-xs font-semibold text-white"
                      >
                        打开工作台
                      </Link>
                    </div>
                    <div className="mt-3 grid gap-2 text-sm text-on-surface-variant md:grid-cols-3">
                      <span>总数：{batch.totalResumes}</span>
                      <span>通过：{batch.passCount}</span>
                      <span>待复核：{batch.reviewCount}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <div className="rounded-[24px] bg-primary px-6 py-6 text-white shadow-ambient">
            <div className="text-xs uppercase tracking-[0.12em] text-white/70">Flow</div>
            <h3 className="mt-2 font-headline text-2xl font-bold">下一步：批量初筛</h3>
            <p className="mt-3 text-sm leading-7 text-white/80">
              批量页会读取这里的默认 AI reviewer 配置，并允许按批次覆盖运行时开关、provider、model 与 key 配置。
            </p>
            <Link
              to={`/batch-screening?jd_title=${encodeURIComponent(decodedTitle)}`}
              className="mt-5 inline-flex rounded-2xl bg-white px-4 py-3 text-sm font-semibold text-primary"
            >
              进入批量初筛
            </Link>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function safeParseJson(value) {
  if (typeof value !== "string") {
    return value || {};
  }
  try {
    return JSON.parse(value || "{}");
  } catch {
    return {};
  }
}

function Banner({ tone, title, detail }) {
  const toneClass =
    tone === "error" ? "bg-error-container text-error" : "bg-emerald-50 text-emerald-800";
  return (
    <div className={`rounded-[20px] px-5 py-4 text-sm ${toneClass}`}>
      <div className="font-semibold">{title}</div>
      {detail ? <div className="mt-1">{detail}</div> : null}
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

function Panel({ title, children }) {
  return (
    <div className="rounded-[24px] bg-surface-container-lowest p-6 shadow-ambient">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{title}</div>
      <div className="mt-4">{children}</div>
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

function EmptyState({ title, description }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-4 text-sm text-on-surface-variant">
      <div className="font-semibold text-on-surface">{title}</div>
      <div className="mt-1 leading-7">{description}</div>
    </div>
  );
}
