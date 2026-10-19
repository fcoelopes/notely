import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AIModelPanel } from "./AIModelPanel";
import type { AIModelConfig, AIModelConfigInput } from "../types";

function config(overrides: Partial<AIModelConfig> = {}): AIModelConfig {
  return {
    provider: "anthropic",
    model: "claude-sonnet-4-5",
    base_url: null,
    enabled: true,
    has_key: true,
    ...overrides,
  };
}

function setup(props: Partial<Parameters<typeof AIModelPanel>[0]> = {}) {
  const onSave = vi.fn<(input: AIModelConfigInput) => Promise<boolean>>(() => Promise.resolve(true));
  const onRemove = vi.fn<() => Promise<boolean>>(() => Promise.resolve(true));
  const onReload = vi.fn();
  render(
    <AIModelPanel
      busy={false}
      config={null}
      error={null}
      loading={false}
      notice={null}
      onReload={onReload}
      onRemove={onRemove}
      onSave={onSave}
      {...props}
    />,
  );
  return { onSave, onRemove, onReload };
}

function submittedInput(onSave: ReturnType<typeof setup>["onSave"]): AIModelConfigInput {
  const [input] = onSave.mock.calls[0] ?? [];
  return input as AIModelConfigInput;
}

afterEach(cleanup);

describe("AIModelPanel", () => {
  it("shows an empty form and says that there is no model configured", () => {
    setup();
    expect(screen.getByText("Nenhum modelo de IA configurado.")).toBeInTheDocument();
    expect(screen.getByLabelText("Modelo")).toHaveValue("");
    expect(screen.getByLabelText("Chave da API")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Cadastrar modelo" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Remover configuração" })).not.toBeInTheDocument();
  });

  it("shows provider, model, base url and that a key exists without exposing the key", () => {
    setup({
      config: config({
        provider: "openai_compatible",
        model: "llama3.1",
        base_url: "http://localhost:11434/v1",
      }),
    });
    // O nome do provedor aparece no resumo e na opção do formulário.
    expect(screen.getAllByText("Compatível com OpenAI").length).toBeGreaterThan(0);
    expect(screen.getByText("llama3.1")).toBeInTheDocument();
    expect(screen.getByText("http://localhost:11434/v1")).toBeInTheDocument();
    expect(screen.getByText("gravada no servidor")).toBeInTheDocument();
    expect(screen.getByText("ativo")).toBeInTheDocument();
    // A chave não é devolvida: o campo começa vazio e o tipo é senha.
    const key = screen.getByLabelText("Chave da API");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("type", "password");
    expect(screen.getByText(/manter a chave já gravada/i)).toBeInTheDocument();
    expect(screen.getByText(/não é exibida de novo/i)).toBeInTheDocument();
  });

  it("keeps the stored key out of the payload when the key field is left blank", async () => {
    const { onSave } = setup({ config: config() });
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "claude-opus-4-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const input = submittedInput(onSave);
    expect(input).toEqual({
      provider: "anthropic",
      model: "claude-opus-4-5",
      base_url: null,
      enabled: true,
    });
    // Sem chave no envio: a que está gravada é mantida pela API.
    expect(input.api_key).toBeUndefined();
  });

  it("requires the base url for openai_compatible and dispenses it for anthropic", async () => {
    const { onSave } = setup({ config: config({ base_url: null }) });
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "llama3.1" } });
    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "openai_compatible" },
    });

    // Sem base URL o envio não acontece, e a tela diz por quê.
    const submit = screen.getByRole("button", { name: "Salvar configuração" });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/exige a base URL antes de gravar/i)).toBeInTheDocument();
    fireEvent.click(submit);
    fireEvent.submit(screen.getByRole("button", { name: "Salvar configuração" }).closest("form") as HTMLFormElement);
    expect(onSave).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Base URL do provedor"), {
      target: { value: " http://localhost:11434/v1 " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave)).toEqual({
      provider: "openai_compatible",
      model: "llama3.1",
      base_url: "http://localhost:11434/v1",
      enabled: true,
    });

    // O provedor oficial dispensa a base URL.
    cleanup();
    const second = setup();
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "claude-sonnet-4-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));
    await waitFor(() => expect(second.onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(second.onSave).base_url).toBeNull();
  });

  it("sends a typed key and clears the field after saving", async () => {
    const { onSave } = setup({ config: config({ has_key: false }) });
    const key = screen.getByLabelText("Chave da API") as HTMLInputElement;
    fireEvent.change(key, { target: { value: "  chave-ficticia-de-teste  " } });

    // O campo é senha e pode ser revelado.
    expect(key).toHaveAttribute("type", "password");
    fireEvent.click(screen.getByRole("button", { name: "Mostrar" }));
    expect(screen.getByLabelText("Chave da API")).toHaveAttribute("type", "text");
    fireEvent.click(screen.getByRole("button", { name: "Ocultar" }));
    expect(screen.getByLabelText("Chave da API")).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave).api_key).toBe("chave-ficticia-de-teste");
    // A chave sai do estado da tela depois do sucesso.
    await waitFor(() => expect(screen.getByLabelText("Chave da API")).toHaveValue(""));
  });

  it("sends the enabled flag with the configuration", async () => {
    const { onSave } = setup({ config: config() });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave).enabled).toBe(false);
  });

  it("shows the api failure and preserves the typed fields", async () => {
    const { onSave } = setup({
      config: config(),
      error: "Não foi possível gravar a configuração.",
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Não foi possível gravar a configuração.");
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "modelo-que-nao-grava" } });
    fireEvent.change(screen.getByLabelText("Chave da API"), { target: { value: "chave-ficticia" } });
    onSave.mockResolvedValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    // O que foi digitado continua no formulário; a chave só sai quando a API confirma.
    expect(screen.getByLabelText("Modelo")).toHaveValue("modelo-que-nao-grava");
    expect(screen.getByLabelText("Chave da API")).toHaveValue("chave-ficticia");
    expect(screen.getByRole("button", { name: "Atualizar" })).toBeInTheDocument();
  });

  it("shows the success message without keeping the key", () => {
    setup({ config: config(), notice: "Configuração salva." });
    expect(screen.getByRole("status")).toHaveTextContent("Configuração salva.");
    expect(screen.getByLabelText("Chave da API")).toHaveValue("");
  });

  it("asks for confirmation before removing and can cancel", async () => {
    const { onRemove } = setup({ config: config({ provider: "openai_compatible", base_url: "http://host/v1" }) });

    fireEvent.click(screen.getByRole("button", { name: "Remover configuração" }));
    expect(screen.getByRole("group", { name: "Confirmar remoção" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
    expect(onRemove).not.toHaveBeenCalled();
    expect(screen.queryByRole("group", { name: "Confirmar remoção" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remover configuração" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirmar remoção" }));
    await waitFor(() => expect(onRemove).toHaveBeenCalledTimes(1));
  });

  it("goes back to the empty state when the configuration is removed", () => {
    const { rerender } = render(
      <AIModelPanel
        busy={false}
        config={config()}
        error={null}
        loading={false}
        notice={null}
        onReload={vi.fn()}
        onRemove={vi.fn(() => Promise.resolve(true))}
        onSave={vi.fn(() => Promise.resolve(true))}
      />,
    );
    expect(screen.getByText("claude-sonnet-4-5")).toBeInTheDocument();

    rerender(
      <AIModelPanel
        busy={false}
        config={null}
        error={null}
        loading={false}
        notice="Configuração removida."
        onReload={vi.fn()}
        onRemove={vi.fn(() => Promise.resolve(true))}
        onSave={vi.fn(() => Promise.resolve(true))}
      />,
    );
    expect(screen.getByText("Nenhum modelo de IA configurado.")).toBeInTheDocument();
    expect(screen.getByLabelText("Modelo")).toHaveValue("");
    expect(screen.queryByRole("button", { name: "Remover configuração" })).not.toBeInTheDocument();
  });
});
