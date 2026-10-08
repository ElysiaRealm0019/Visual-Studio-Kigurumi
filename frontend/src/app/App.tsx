import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { ProjectsPage } from "../features/workspace/ProjectsPage";
import { WorkspacePage } from "../features/workspace/WorkspacePage";
import { AdminAuditPage } from "../pages/AdminAuditPage";

function LegacyConversationRedirect() {
  const { conversationId } = useParams();
  return <Navigate replace to={`/p/${conversationId}`} />;
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AdminAuditPage />} path="/admin/audit" />
        <Route element={<WorkspacePage />} path="/p/:projectId" />
        <Route element={<LegacyConversationRedirect />} path="/c/:conversationId" />
        <Route element={<ProjectsPage />} path="*" />
      </Routes>
    </BrowserRouter>
  );
}
