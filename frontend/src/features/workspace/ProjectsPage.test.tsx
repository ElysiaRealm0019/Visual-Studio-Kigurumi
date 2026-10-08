import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import { ProjectsPage } from "./ProjectsPage";

const project = {
  id: "p1",
  title: "黑双马尾",
  created_at: "2026-10-08T00:00:00Z",
  status: "idle",
  thumbnail_url: null,
  version_count: 2,
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function openMenu(item: string) {
  fireEvent.click(await screen.findByRole("button", { name: "项目操作" }));
  fireEvent.click(screen.getByRole("button", { name: item }));
}

describe("ProjectsPage", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("zh-CN");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("deletes a project through the in-app dialog without using window.confirm", async () => {
    let projects = [project];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "DELETE") {
        projects = [];
        return json({ deleted: true });
      }
      return json(projects);
    });
    vi.stubGlobal("fetch", fetchMock);
    const nativeConfirm = vi.spyOn(window, "confirm");
    renderPage();

    await openMenu("删除项目");
    expect(screen.getByText("确定删除“黑双马尾”？项目里的所有版本都会被删除，无法撤销。")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "删除项目" }).at(-1)!);

    await waitFor(() => expect(screen.queryByText("黑双马尾")).toBeNull());
    expect(fetchMock.mock.calls.some(([url, init]) => url === "/api/agent/conversations/p1" && init?.method === "DELETE")).toBe(true);
    expect(nativeConfirm).not.toHaveBeenCalled();
  });

  it("keeps the dialog open and explains why when the project is still running", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) =>
        init?.method === "DELETE" ? json({ detail: "conversation_running" }, 409) : json([project]),
      ),
    );
    renderPage();

    await openMenu("删除项目");
    fireEvent.click(screen.getAllByRole("button", { name: "删除项目" }).at(-1)!);

    expect((await screen.findByRole("alert")).textContent).toBe("上一步还在进行中，请稍等。");
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renames a project through the in-app dialog", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "PATCH"
        ? json({
            id: "p1",
            title: "新名字",
            created_at: project.created_at,
            locale: "zh-CN",
            status: "idle",
            running: false,
            last_seq: 0,
            events: [],
            state: { references: [], images: [], current_front_id: null, approved_front_id: null },
          })
        : json([project]),
    );
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    await openMenu("重命名");
    const input = screen.getByRole("textbox", { name: "重命名" });
    expect((input as HTMLInputElement).value).toBe("黑双马尾");
    fireEvent.change(input, { target: { value: " 新名字 " } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const patch = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH");
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ title: "新名字" });
  });
});
