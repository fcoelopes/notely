import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AIModelPanel } from "./AIModelPanel";
import { ApiError } from "../lib/api";
import type { AIModelConfig, AIModelConfigInput, AIModelListInput, AIModelOption } from "../types";

const MODELS: AIModelOption[] = [
  { id: "claude-sonnet-4-5", display_name: "Claude Sonnet 4.5" },
  { id: "claude-haiku-4-5", display_name: null },
];

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

function setup(
  props: Partial<Parameters<typeof AIModelPanel>[0]> = {},
  models: AIModelOption[] = [],
) {
  const onSave = vi.fn<(input: AIModelConfigInput) => Promise<boolean>>(() => Promise.resolve(true));
  const onRemove = vi.fn<() => Promise<boolean>>(() => Promise.resolve(true));
  const onListModels = vi.fn<(input: AIModelListInput) => Promise<AIModelOption[]>>(() =>
    Promise.resolve(models),
  );
  const onReload = vi.fn();
  render(
    <AIModelPanel
      busy={false}
      config={null}
      error={null}
      loading={false}
      notice={null}
      onListModels={onListModels}
      onReload={onReload}
      onRemove={onRemove}
      onSave={onSave}
      {...props}
    />,
  );
  return { onSave, onRemove, onListModels, onReload };
}

function submittedInput(onSave: ReturnType<typeof setup>["onSave"]): AIModelConfigInput {
  const [input] = onSave.mock.calls[0] ?? [];
  return input as AIModelConfigInput;
}

afterEach(cleanup);

describe("AIModelPanel", () => {
  it("shows an empty form and says that there is no model configured", () => {
    const { onListModels } = setup();
    expect(screen.getByText("Nenhum modelo de IA configurado.")).toBeInTheDocument();
    expect(screen.getByLabelText("Modelo")).toHaveValue("");
    expect(screen.getByLabelText("Chave da API")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Cadastrar modelo" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Remover configuração" })).not.toBeInTheDocument();
    // Sem chave gravada a lista não é pedida sozinha: quem dispara é o botão.
    expect(onListModels).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Carregar modelos" })).toBeInTheDocument();
  });

  it("shows provider, model, endpoint and that a key exists without exposing the key", () => {
    setup({
      config: config({
        provider: "openai_compatible",
        model: "llama3.1",
        base_url: "http://localhost:11434/v1/chat/completions",
      }),
    });
    // O nome do provedor aparece no resumo e na opção do formulário.
    expect(screen.getAllByText("Compatível com OpenAI").length).toBeGreaterThan(0);
    expect(screen.getByText("llama3.1")).toBeInTheDocument();
    expect(screen.getByText("http://localhost:11434/v1/chat/completions")).toBeInTheDocument();
    expect(screen.getByText("Endpoint")).toBeInTheDocument();
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

  it("requires the chat completions url for openai_compatible and dispenses it for anthropic", async () => {
    const { onSave } = setup({ config: config({ base_url: null }) });
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "llama3.1" } });
    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "openai_compatible" },
    });

    // O campo diz que é a URL de /chat/completions, e sem ela o envio não acontece.
    const endpoint = screen.getByLabelText("Endpoint do provedor em /chat/completions");
    expect(endpoint).toHaveAttribute(
      "placeholder",
      "ex.: http://localhost:11434/v1/chat/completions",
    );
    const submit = screen.getByRole("button", { name: "Salvar configuração" });
    expect(submit).toBeDisabled();
    expect(screen.getByText(/exige a URL de \/chat\/completions antes de gravar/i)).toBeInTheDocument();
    fireEvent.click(submit);
    fireEvent.submit(
      screen.getByRole("button", { name: "Salvar configuração" }).closest("form") as HTMLFormElement,
    );
    expect(onSave).not.toHaveBeenCalled();

    fireEvent.change(endpoint, {
      target: { value: " http://localhost:11434/v1/chat/completions " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave)).toEqual({
      provider: "openai_compatible",
      model: "llama3.1",
      base_url: "http://localhost:11434/v1/chat/completions",
      enabled: true,
    });

    // O provedor oficial dispensa o endpoint.
    cleanup();
    const second = setup();
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "claude-sonnet-4-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));
    await waitFor(() => expect(second.onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(second.onSave).base_url).toBeNull();
  });

  it("lists the provider models automatically when a configuration exists", async () => {
    const { onListModels, onSave } = setup({ config: config() }, MODELS);

    // Já existe configuração, então a lista é pedida sozinha, com o que está gravado.
    await waitFor(() => expect(onListModels).toHaveBeenCalledTimes(1));
    expect(onListModels).toHaveBeenCalledWith({ provider: "anthropic", base_url: null });

    // O campo de modelo vira a lista do provedor, com o modelo gravado selecionado.
    const field = await screen.findByLabelText("Modelo");
    expect(field.tagName).toBe("SELECT");
    expect(field).toHaveValue("claude-sonnet-4-5");
    expect(screen.getByRole("option", { name: "Claude Sonnet 4.5" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "claude-haiku-4-5" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "outro (digitar)" })).toBeInTheDocument();

    fireEvent.change(field, { target: { value: "claude-haiku-4-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave).model).toBe("claude-haiku-4-5");
  });

  it("lists on demand with the typed key while there is no saved configuration", async () => {
    const { onListModels } = setup({}, MODELS);
    expect(screen.getByLabelText("Modelo").tagName).toBe("INPUT");
    expect(onListModels).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Chave da API"), {
      target: { value: "  chave-ficticia  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Carregar modelos" }));

    await waitFor(() => expect(onListModels).toHaveBeenCalledTimes(1));
    // A chave digitada vale para a listagem e não é gravada por ela.
    expect(onListModels).toHaveBeenCalledWith({
      provider: "anthropic",
      api_key: "chave-ficticia",
      base_url: null,
    });
    await waitFor(() => expect(screen.getByLabelText("Modelo").tagName).toBe("SELECT"));
    // Sem modelo escolhido a lista abre no convite, e não num valor inventado.
    expect(screen.getByLabelText("Modelo")).toHaveValue("");
    expect(screen.getByRole("option", { name: "Claude Sonnet 4.5" })).toBeInTheDocument();
  });

  it("sends the typed endpoint when the list is requested for a compatible provider", async () => {
    const { onListModels } = setup();
    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "openai_compatible" },
    });
    fireEvent.change(screen.getByLabelText("Endpoint do provedor em /chat/completions"), {
      target: { value: "http://localhost:11434/v1/chat/completions" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Carregar modelos" }));

    await waitFor(() => expect(onListModels).toHaveBeenCalledTimes(1));
    expect(onListModels).toHaveBeenCalledWith({
      provider: "openai_compatible",
      api_key: undefined,
      base_url: "http://localhost:11434/v1/chat/completions",
    });
  });

  it("goes back to the free text field when 'outro (digitar)' is chosen", async () => {
    const { onSave } = setup({ config: config() }, MODELS);
    await waitFor(() => expect(screen.getByLabelText("Modelo").tagName).toBe("SELECT"));

    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "__other__" } });

    // O campo de texto volta com o modelo atual, e o que for digitado é o que se grava.
    const typed = screen.getByLabelText("Modelo");
    expect(typed.tagName).toBe("INPUT");
    expect(typed).toHaveValue("claude-sonnet-4-5");
    fireEvent.change(typed, { target: { value: "modelo-local:7b" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave).model).toBe("modelo-local:7b");
  });

  it("keeps the typed model when the provider list fails", async () => {
    const { onListModels, onSave } = setup();
    onListModels.mockRejectedValue(
      new ApiError(409, "the model provider has no stored key"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Carregar modelos" }));

    // A falha aparece e não fecha a porta: o nome continua digitável e gravável.
    expect(
      await screen.findByText(/Não há chave para listar os modelos/i),
    ).toBeInTheDocument();
    const field = screen.getByLabelText("Modelo");
    expect(field.tagName).toBe("INPUT");
    fireEvent.change(field, { target: { value: "claude-sonnet-4-5" } });
    fireEvent.change(screen.getByLabelText("Chave da API"), {
      target: { value: "chave-ficticia" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave).model).toBe("claude-sonnet-4-5");
  });

  it("reports a provider failure when the list does not come, without blocking typing", async () => {
    const { onListModels, onSave } = setup({}, []);
    onListModels.mockRejectedValue(new ApiError(502, "provider exploded"));
    fireEvent.click(screen.getByRole("button", { name: "Carregar modelos" }));

    expect(
      await screen.findByText(/O provedor não respondeu à lista de modelos/i),
    ).toBeInTheDocument();
    // O erro do provedor não repete nada além do que a tela já sabe.
    expect(screen.queryByText(/provider exploded/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "llama3.1" } });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  });

  it("keeps the free text field when the configuration model is not in the list", async () => {
    setup({ config: config({ model: "claude-que-nao-esta-na-lista" }) }, MODELS);

    await waitFor(() => expect(screen.getByText(/Carregar modelos/)).toBeInTheDocument());
    // O provedor não devolveu o modelo gravado: apagar o que está configurado seria pior.
    expect(screen.getByLabelText("Modelo").tagName).toBe("INPUT");
    expect(screen.getByLabelText("Modelo")).toHaveValue("claude-que-nao-esta-na-lista");
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
    const { onRemove } = setup({
      config: config({
        provider: "openai_compatible",
        base_url: "http://host/v1/chat/completions",
      }),
    });

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
        onListModels={vi.fn(() => Promise.resolve([]))}
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
        onListModels={vi.fn(() => Promise.resolve([]))}
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
