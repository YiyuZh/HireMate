import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import AppShell from "../components/AppShell";
import { useSession } from "../components/RequireAuth";
import { api } from "../lib/api";
import { normalizeJobs } from "../lib/viewModels";

export default function JobsPage() {
  const session = useSession();
  const jobsQuery = useQuery({
    queryKey: ["jobs"],
    queryFn: async () => normalizeJobs(await api.get("/api/jobs"))
  });

  const jobs = jobsQuery.data || [];

  return (
    <AppShell
      user={session.data}
      eyebrow="Job Config"
      title="岗位配置"
      subtitle="维护岗位 JD、基础权重和 AI reviewer 默认配置，作为批量初筛和候选人工作台的统一起点。"
    >
      <div className="grid gap-6 lg:grid-cols-[420px_minmax(0,1fr)]">
        <section className="space-y-4">
          {jobsQuery.isLoading ? (
            <InfoPanel title="正在加载岗位配置" description="系统正在读取岗位库，请稍候。" />
          ) : null}

          {jobsQuery.isError ? (
            <InfoPanel title="岗位配置读取失败" description="请检查登录状态或稍后刷新后重试。" tone="error" />
          ) : null}

          {!jobsQuery.isLoading && !jobs.length ? (
            <InfoPanel title="还没有岗位配置" description="你可以先在后端接口或后续页面中创建第一个岗位配置。" />
          ) : null}

          {jobs.map((job) => (
            <Link
              key={job.title}
              to={`/jobs/${encodeURIComponent(job.title)}`}
              className="block rounded-[24px] bg-surface-container-lowest p-5 shadow-ambient transition hover:-translate-y-0.5"
            >
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="inline-flex rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">
                    {job.title}
                  </div>
                  <h3 className="mt-3 font-headline text-2xl font-bold">{job.title}</h3>
                </div>
                <div className="text-right text-xs text-on-surface-variant">
                  <div>Openings</div>
                  <div className="mt-1 text-xl font-bold text-on-surface">{job.openings}</div>
                </div>
              </div>
              <p className="mt-4 line-clamp-4 text-sm leading-7 text-on-surface-variant">{job.jdTextPreview}</p>
              <div className="mt-5 grid grid-cols-3 gap-3 text-xs">
                <MetricBadge label="通过" value={job.latestBatch.passCount} tone="emerald" />
                <MetricBadge label="待复核" value={job.latestBatch.reviewCount} tone="amber" />
                <MetricBadge label="淘汰" value={job.latestBatch.rejectCount} tone="rose" />
              </div>
            </Link>
          ))}
        </section>

        <section className="rounded-[24px] bg-surface-container-lowest p-8 shadow-ambient">
          <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">Overview</div>
          <h2 className="mt-3 font-headline text-4xl font-extrabold">岗位配置总览</h2>
          <p className="mt-4 max-w-3xl text-sm leading-7 text-on-surface-variant">
            新前端已经开始消费稳定的后端 view model。这里展示的是岗位摘要、批次快照和 AI 默认配置，
            不再直接依赖 legacy 原始结构。
          </p>
          <div className="mt-8 grid gap-4 md:grid-cols-3">
            <InfoCard label="岗位总数" value={jobs.length} />
            <InfoCard
              label="最近批次总数"
              value={jobs.reduce((sum, item) => sum + item.latestBatch.totalResumes, 0)}
            />
            <InfoCard
              label="最近通过人数"
              value={jobs.reduce((sum, item) => sum + item.latestBatch.passCount, 0)}
            />
          </div>
          <div className="mt-10 rounded-[24px] bg-primary px-6 py-6 text-white">
            <div className="text-xs uppercase tracking-[0.12em] text-white/70">Next Step</div>
            <h3 className="mt-2 font-headline text-2xl font-bold">从岗位配置直接进入批量初筛</h3>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-white/80">
              下一轮会继续把岗位详情页和批量初筛页打磨成同一套 cockpit 语言，同时保留现有规则评分和人工决策边界。
            </p>
          </div>
        </section>
      </div>
    </AppShell>
  );
}

function InfoPanel({ title, description, tone = "default" }) {
  const toneClass =
    tone === "error" ? "bg-error-container text-error" : "bg-surface-container-lowest text-on-surface";
  return (
    <div className={`rounded-[24px] p-5 shadow-ambient ${toneClass}`}>
      <div className="font-semibold">{title}</div>
      <div className="mt-2 text-sm leading-7">{description}</div>
    </div>
  );
}

function InfoCard({ label, value }) {
  return (
    <div className="rounded-[20px] bg-surface-container-low p-5">
      <div className="text-xs uppercase tracking-[0.12em] text-on-surface-variant">{label}</div>
      <div className="mt-3 font-headline text-3xl font-extrabold">{value}</div>
    </div>
  );
}

function MetricBadge({ label, value, tone }) {
  const tones = {
    emerald: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    rose: "bg-rose-50 text-rose-700"
  };
  return (
    <div className="rounded-2xl bg-surface-container-low p-3">
      <div className="text-[11px] uppercase tracking-[0.12em] text-on-surface-variant">{label}</div>
      <div className={`mt-2 inline-flex rounded-full px-3 py-1 text-sm font-semibold ${tones[tone]}`}>{value}</div>
    </div>
  );
}
