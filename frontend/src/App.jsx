import { Navigate, Route, Routes } from "react-router-dom";

import AppShell from "./components/AppShell";
import RequireAuth, { RequireAdmin, useSession } from "./components/RequireAuth";
import AdminSystemHealthPage from "./pages/AdminSystemHealthPage";
import BatchScreeningPage from "./pages/BatchScreeningPage";
import JobDetailPage from "./pages/JobDetailPage";
import JobsPage from "./pages/JobsPage";
import LoginPage from "./pages/LoginPage";
import WorkbenchPage from "./pages/WorkbenchPage";

function RootRedirect() {
  const session = useSession();
  if (session.isLoading) {
    return <div className="flex min-h-screen items-center justify-center">正在加载...</div>;
  }
  if (session.isError) {
    return <Navigate to="/login" replace />;
  }
  return <Navigate to="/jobs" replace />;
}

function PlaceholderArchive() {
  const session = useSession();
  return (
    <AppShell
      user={session.data}
      eyebrow="Archive"
      title="Legacy Rollback Area"
      subtitle="旧 Streamlit 仍保留作回滚入口，但默认流量会逐步迁移到新的 React + FastAPI 主链路。"
    >
      <div className="rounded-[24px] bg-surface-container-lowest p-10 shadow-ambient">
        这里预留给后续 legacy 回滚入口和迁移状态说明。
      </div>
    </AppShell>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<RootRedirect />} />
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/jobs"
        element={
          <RequireAuth>
            <JobsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/jobs/:jdTitle"
        element={
          <RequireAuth>
            <JobDetailPage />
          </RequireAuth>
        }
      />
      <Route
        path="/batch-screening"
        element={
          <RequireAuth>
            <BatchScreeningPage />
          </RequireAuth>
        }
      />
      <Route
        path="/workbench"
        element={
          <RequireAuth>
            <WorkbenchPage />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/system-health"
        element={
          <RequireAuth>
            <RequireAdmin>
              <AdminSystemHealthPage />
            </RequireAdmin>
          </RequireAuth>
        }
      />
      <Route
        path="/archive"
        element={
          <RequireAuth>
            <PlaceholderArchive />
          </RequireAuth>
        }
      />
    </Routes>
  );
}
