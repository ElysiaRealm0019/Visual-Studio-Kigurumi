import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import i18n from "../../i18n";
import type { BackendSettings } from "./settingsApi";
import { SettingsPage } from "./SettingsPage";

const initial: BackendSettings = {
  writable: true,
  values: {
    agent_llm_provider: "openai_compatible",
    agent_llm_base_url: "https://example.test/v1",
    agent_llm_model: "chat-model",
    agent_llm_extra_body: '{"thinking": {"type": "disabled"}}',
    agent_claude_code_model: "",
    llm_provider: "codex",
    analysis_llm_base_url: "",
    analysis_llm_model: "",
    image_provider: "codex",
    siliconflow_image_model: "sf-model",
    ark_image_model: "ark-model",
  },
  overridden: [],
  options: {
    agent_llm_provider: ["openai_compatible", "claude_code"],
    llm_provider: ["openai_compatible", "codex", "claude_code"],
    image_provider: ["codex", "codex_bridge", "siliconflow", "ark"],
  },
  secrets: {
    agent_llm_api_key: { set: false, source: "", hint: "" },
    analysis_llm_api_key: { set: false, source: "", hint: "" },
    siliconflow_api_key: { set: true, source: "env", hint: "…0000" },
    ark_api_key: { set: false, source: "", hint: "" },
    codex_bridge_token: { set: false, source: "", hint: "" },
  },
  status: { agent_llm_key: true, analysis_llm_key: false, claude_cli: true, codex_cli: true, codex_bridge: false, siliconflow_key: true, ark_key: false },
};

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SettingsPage", () => {
  beforeAll(async () => {
    await i18n.changeLanguage("zh-CN");
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("saves only the changed backend values", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        const saved = { ...initial, values: { ...initial.values, image_provider: "siliconflow" }, overridden: ["image_provider"] };
        return new Response(JSON.stringify(saved));
      }
      return new Response(JSON.stringify(initial));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const siliconflow = await screen.findByRole("radio", { name: "SiliconFlow" });
    expect(screen.getByRole("button", { name: "保存" })).toHaveProperty("disabled", true);
    fireEvent.click(siliconflow);
    expect(screen.getByDisplayValue("sf-model")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await screen.findByText("已保存，下一次请求起生效");
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ values: { image_provider: "siliconflow" } });
    expect(screen.getByText("已覆盖 .env")).toBeTruthy();
  });

  it("sends a typed API key once and never shows the saved one", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        const saved = {
          ...initial,
          values: { ...initial.values, image_provider: "ark" },
          secrets: { ...initial.secrets, ark_api_key: { set: true, source: "override", hint: "…9999" } },
          overridden: ["ark_api_key", "image_provider"],
          status: { ...initial.status, ark_key: true },
        };
        return new Response(JSON.stringify(saved));
      }
      return new Response(JSON.stringify(initial));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    fireEvent.click(await screen.findByRole("radio", { name: "火山方舟 Seedream" }));
    expect(screen.getByText("需要填写方舟 API Key")).toBeTruthy();
    fireEvent.change(screen.getAllByPlaceholderText("粘贴 API Key").at(-1)!, { target: { value: " ark-new-key-9999 " } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await screen.findByText(/已配置 …9999（在本页保存）/);
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put?.[1]?.body))).toEqual({ values: { image_provider: "ark", ark_api_key: "ark-new-key-9999" } });
    expect((screen.getByPlaceholderText("留空保持不变，输入新 Key 则替换") as HTMLInputElement).value).toBe("");

    fireEvent.click(screen.getByRole("button", { name: "清除本页保存的 Key" }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")).toHaveLength(2));
    const clear = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT")[1];
    expect(JSON.parse(String(clear[1]?.body))).toEqual({ values: { ark_api_key: null } });
  });

  it("lets the analysis LLM use an OpenAI-compatible API that defaults to the chat assistant", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(initial)));
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const analysis = await screen.findByRole("radiogroup", { name: "参考图分析 LLM" });
    const radio = analysis.querySelector<HTMLInputElement>('input[value="openai_compatible"]')!;
    fireEvent.click(radio);

    expect(screen.getByPlaceholderText("留空沿用对话助手：https://example.test/v1")).toBeTruthy();
    expect(screen.getByPlaceholderText("留空沿用对话助手：chat-model")).toBeTruthy();
    expect(screen.getByText("需要填写 API Key（或沿用对话助手的 Key）")).toBeTruthy();
    expect(screen.getByText("模型必须支持图片输入（多模态），否则分析会失败。")).toBeTruthy();
  });

  it("tests the saved chat assistant and explains each check", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/settings/probe/agent" && init?.method === "POST") {
        return new Response(
          JSON.stringify({
            ok: true,
            model: "glm-5",
            base_url: "https://example.test/v1",
            latency_ms: 812,
            checks: [
              { id: "connection", status: "ok", detail: "" },
              { id: "extra_body", status: "warn", detail: "thinking" },
              { id: "tools", status: "ok", detail: "" },
            ],
          }),
        );
      }
      return new Response(JSON.stringify(initial));
    });
    vi.stubGlobal("fetch", fetchMock);
    renderPage();

    const button = await screen.findByRole("button", { name: "测试连接" });
    fireEvent.change(screen.getByDisplayValue("chat-model"), { target: { value: "other" } });
    expect(button).toHaveProperty("disabled", true);
    expect(screen.getByText("有未保存的修改，先保存再测试。")).toBeTruthy();
    fireEvent.change(screen.getByDisplayValue("other"), { target: { value: "chat-model" } });
    fireEvent.click(button);

    await screen.findByText("glm-5 可用 · 812 ms");
    expect(screen.getByText("模型不支持额外参数 thinking，已自动去掉。建议清空“额外请求参数”。")).toBeTruthy();
    expect(screen.getByText("支持工具调用")).toBeTruthy();
  });

  it("explains a failed connection", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/settings/probe/agent"
          ? new Response(
              JSON.stringify({ ok: false, model: "glm-5", base_url: "x", latency_ms: null, checks: [{ id: "connection", status: "fail", detail: "auth: HTTP 401: bad key" }] }),
            )
          : new Response(JSON.stringify(initial)),
      ),
    );
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "测试连接" }));

    await screen.findByText("glm-5 不可用");
    expect(screen.getByText("Key 无效或没有权限（HTTP 401: bad key）")).toBeTruthy();
  });

  it("warns when the selected backend is not configured and disables editing when read-only", async () => {
    const readOnly = { ...initial, writable: false, values: { ...initial.values, image_provider: "ark" } };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(readOnly))));
    renderPage();

    await screen.findByText("需要填写方舟 API Key");
    expect(screen.getByText("生产环境下设置为只读，请修改 .env。")).toBeTruthy();
    await waitFor(() => expect(screen.getAllByRole("radio").every((radio) => (radio as HTMLInputElement).disabled)).toBe(true));
  });
});
