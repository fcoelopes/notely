import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  deleteAIModelConfig,
  getAIModelConfig,
  saveAIModelConfig,
} from "./api";

/** Resposta mínima: o helper só lê `ok`, `status` e `json`. */
function response(status: number, body?: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const fetchMock = vi.fn();

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function stubFetch(): void {
  vi.stubGlobal("fetch", fetchMock);
}

const saved = {
  provider: "openai_compatible",
  model: "llama3.1",
  base_url: "http://localhost:11434/v1",
  enabled: true,
  has_key: true,
};

describe("api da configuração de modelo", () => {
  it("lê a configuração devolvida pela API", async () => {
    stubFetch();
    fetchMock.mockResolvedValue(response(200, saved));
    await expect(getAIModelConfig()).resolves.toEqual(saved);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai-config",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("trata o 404 da leitura como ausência de configuração", async () => {
    stubFetch();
    fetchMock.mockResolvedValue(response(404, { detail: "no model is configured" }));
    // Sem configuração não é erro: o painel mostra o formulário vazio.
    await expect(getAIModelConfig()).resolves.toBeNull();
  });

  it("deixa de pé as outras falhas da leitura", async () => {
    stubFetch();
    fetchMock.mockResolvedValue(response(503, { detail: "cifra indisponível" }));
    const failure = await getAIModelConfig().catch((caught: unknown) => caught);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(503);
    expect((failure as ApiError).message).toBe("cifra indisponível");
  });

  it("grava a configuração com PUT, sem nunca devolver a chave", async () => {
    stubFetch();
    fetchMock.mockResolvedValue(response(200, saved));
    const input = {
      provider: "openai_compatible" as const,
      model: "llama3.1",
      base_url: "http://localhost:11434/v1",
      enabled: true,
      api_key: "chave-ficticia-de-teste",
    };
    await expect(saveAIModelConfig(input)).resolves.toEqual(saved);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai-config",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify(input),
        credentials: "include",
      }),
    );
  });

  it("remove a configuração com DELETE e aceita o 204", async () => {
    stubFetch();
    fetchMock.mockResolvedValue(response(204));
    await expect(deleteAIModelConfig()).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai-config",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
