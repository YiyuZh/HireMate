import { Navigate, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import AppShell from "./AppShell";
import { api } from "../lib/api";

export function useSession() {
  return useQuery({
    queryKey: ["session"],
    queryFn: () => api.get("/api/auth/me")
  });
}

export default function RequireAuth({ children }) {
  const location = useLocation();
  const session = useSession();

  if (session.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-on-surface-variant">
        正在加载工作区...
      </div>
    );
  }

  if (session.isError) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }

  return children;
}

export function RequireAdmin({ children }) {
  const session = useSession();

  if (session.isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-on-surface-variant">
        正在校验管理员权限...
      </div>
    );
  }

  if (session.isError) {
    return <Navigate to="/login" replace />;
  }

  if (!session.data?.is_admin) {
    return (
      <AppShell
        user={session.data}
        eyebrow="Admin"
        title="无权限访问"
        subtitle="当前账号不是管理员。你仍然可以继续使用岗位配置、批量初筛和候选人工作台。"
      >
        <div className="rounded-[24px] bg-surface-container-lowest p-10 shadow-ambient">
          <div className="text-sm leading-7 text-on-surface-variant">
            该区域仅对管理员开放。请使用管理员账号登录，或联系系统管理员为当前账号开通权限。
          </div>
        </div>
      </AppShell>
    );
  }

  return children;
}
