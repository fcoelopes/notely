import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AIModelPanel } from "./AIModelPanel";
import { ApiError } from "../lib/api";
import { MODEL_SUGGESTIONS } from "../lib/modelCatalog";
import type { AIModelConfig, AIModelConfigInput, AIModelListInput, AIModelOption } from "../types";

const MODELS: AIModelOption[] = [
  { id: "claude-sonnet-4-5", display_name: "Claude Sonnet 4.5 (provedor)" },
  { id: "claude-opus-4-5", display_name: "Claude Opus 4.5" },
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
  listingError: ApiError | null = null,
) {
  const onSave = vi.fn<(input: AIModelConfigInput) => Promise<boolean>>(() => Promise.resolve(true));
  const onRemove = vi.fn<() => Promise<boolean>>(() => Promise.resolve(true));
  const onListModels = vi.fn<(input: AIModelListInput) => Promise<AIModelOption[]>>(() =>
    listingError ? Promise.reject(listingError) : Promise.resolve(models),
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

/** Valores das opções oferecidas no campo de modelo, na ordem em que aparecem. */
function optionValues(): string[] {
  const field = screen.getByLabelText("Modelo");
  return Array.from(field.querySelectorAll("option")).map((option) => option.value);
}

/** Rótulos dos grupos de opções: separa o que veio do provedor do que é sugestão local. */
function optionGroups(): string[] {
  const field = screen.getByLabelText("Modelo");
  return Array.from(field.querySelectorAll("optgroup")).map((group) => group.label);
}

afterEach(cleanup);

describe("AIModelPanel", () => {
  it("shows an empty form with the provider model list and no call at all", () => {
    const { onListModels } = setup();

    expect(screen.getByText("Nenhum modelo de IA configurado.")).toBeInTheDocument();
    // O campo de modelo já é a lista do provedor escolhido: nenhuma chave, nenhuma chamada.
    const field = screen.getByLabelText("Modelo");
    expect(field.tagName).toBe("SELECT");
    expect(field).toHaveValue("");
    expect(optionValues()).toEqual([
      "",
      ...MODEL_SUGGESTIONS.anthropic.map((suggestion) => suggestion.id),
      "__other__",
    ]);
    expect(screen.getByRole("option", { name: "Claude Sonnet 4.5" })).toBeInTheDocument();
    expect(screen.getByLabelText("Chave da API")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Cadastrar modelo" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Remover configuração" })).not.toBeInTheDocument();
    expect(onListModels).not.toHaveBeenCalled();
  });

  it("switches the model list with the chosen provider, still without a call", () => {
    const { onListModels } = setup();
    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "openai_compatible" },
    });

    // A troca de provedor troca a lista na hora, e o modelo do provedor anterior sai do campo.
    expect(screen.getByLabelText("Modelo").tagName).toBe("SELECT");
    expect(screen.getByLabelText("Modelo")).toHaveValue("");
    expect(optionValues()).toEqual([
      "",
      ...MODEL_SUGGESTIONS.openai_compatible.map((suggestion) => suggestion.id),
      "__other__",
    ]);
    expect(screen.getByRole("option", { name: "GPT-4o" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Claude Sonnet 4.5" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "anthropic" },
    });
    expect(screen.getByRole("option", { name: "Claude Opus 4.1" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "GPT-4o" })).not.toBeInTheDocument();
    expect(onListModels).not.toHaveBeenCalled();
  });

  it("keeps the saved model when coming back to its own provider", () => {
    setup({
      config: config({
        provider: "openai_compatible",
        model: "llama3.1",
        base_url: "http://localhost:11434/v1/chat/completions",
      }),
    });
    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "anthropic" },
    });
    expect(screen.getByLabelText("Modelo")).toHaveValue("");

    // Voltar ao provedor da configuração devolve o modelo gravado, mesmo fora do catálogo.
    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "openai_compatible" },
    });
    expect(screen.getByLabelText("Modelo")).toHaveValue("llama3.1");
    expect(screen.getByRole("option", { name: "llama3.1" })).toBeInTheDocument();
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
    // O modelo gravado aparece no resumo e continua selecionado na lista, mesmo fora dela.
    expect(screen.getAllByText("llama3.1").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Modelo")).toHaveValue("llama3.1");
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
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "claude-opus-4-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const input = submittedInput(onSave);
    expect(input).toEqual({
      provider: "anthropic",
      model: "claude-opus-4-1",
      base_url: null,
      enabled: true,
    });
    // Sem chave no envio: a que está gravada é mantida pela API.
    expect(input.api_key).toBeUndefined();
  });

  it("requires the chat completions url for openai_compatible and dispenses it for anthropic", async () => {
    const first = setup();
    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "openai_compatible" },
    });
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "gpt-4o" } });

    // O campo diz que é a URL de /chat/completions, e sem ela o envio não acontece.
    const endpoint = screen.getByLabelText("Endpoint do provedor em /chat/completions");
    expect(endpoint).toHaveAttribute(
      "placeholder",
      "ex.: http://localhost:11434/v1/chat/completions",
    );
    const submit = screen.getByRole("button", { name: "Cadastrar modelo" });
    expect(submit).toBeDisabled();
    expect(
      screen.getByText(/exige a URL de \/chat\/completions antes de gravar/i),
    ).toBeInTheDocument();
    fireEvent.submit(submit.closest("form") as HTMLFormElement);
    expect(first.onSave).not.toHaveBeenCalled();

    fireEvent.change(endpoint, {
      target: { value: " http://localhost:11434/v1/chat/completions " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));
    await waitFor(() => expect(first.onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(first.onSave)).toEqual({
      provider: "openai_compatible",
      model: "gpt-4o",
      base_url: "http://localhost:11434/v1/chat/completions",
      enabled: true,
    });

    // O provedor oficial dispensa o endpoint.
    cleanup();
    const second = setup();
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "claude-haiku-4-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));
    await waitFor(() => expect(second.onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(second.onSave).base_url).toBeNull();
  });

  it("saves a model chosen from the list with a typed key without consulting the provider", async () => {
    const { onSave, onListModels } = setup();
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "claude-sonnet-4-5" } });
    fireEvent.change(screen.getByLabelText("Chave da API"), {
      target: { value: "  chave-ficticia  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave)).toEqual({
      provider: "anthropic",
      model: "claude-sonnet-4-5",
      base_url: null,
      enabled: true,
      api_key: "chave-ficticia",
    });
    // Nada de consulta ao provedor: a escolha veio da lista padrão.
    expect(onListModels).not.toHaveBeenCalled();
    // A chave sai do estado da tela depois do sucesso.
    await waitFor(() => expect(screen.getByLabelText("Chave da API")).toHaveValue(""));
  });

  it("saves a model from a freshly refreshed provider list", async () => {
    const { onSave, onListModels } = setup({ config: config() }, MODELS);

    fireEvent.click(screen.getByRole("button", { name: "Atualizar do provedor" }));
    await waitFor(() => expect(onListModels).toHaveBeenCalledTimes(1));
    // Com configuração salva a consulta usa o que está gravado, sem chave digitada.
    expect(onListModels).toHaveBeenCalledWith({ provider: "anthropic", base_url: null });

    const field = await screen.findByLabelText("Modelo");
    expect(field).toHaveValue("claude-sonnet-4-5");
    fireEvent.change(field, { target: { value: "claude-opus-4-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave).model).toBe("claude-opus-4-5");
  });

  it("sends the typed key and endpoint when the provider is consulted", async () => {
    const { onListModels } = setup();
    fireEvent.change(screen.getByLabelText("Provedor do modelo"), {
      target: { value: "openai_compatible" },
    });
    fireEvent.change(screen.getByLabelText("Endpoint do provedor em /chat/completions"), {
      target: { value: "http://localhost:11434/v1/chat/completions" },
    });
    fireEvent.change(screen.getByLabelText("Chave da API"), {
      target: { value: "  chave-ficticia  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Atualizar do provedor" }));

    await waitFor(() => expect(onListModels).toHaveBeenCalledTimes(1));
    expect(onListModels).toHaveBeenCalledWith({
      provider: "openai_compatible",
      api_key: "chave-ficticia",
      base_url: "http://localhost:11434/v1/chat/completions",
    });
  });

  it("merges the provider list into the suggestions without duplicating", async () => {
    const { onListModels } = setup({ config: config() }, MODELS);
    fireEvent.click(screen.getByRole("button", { name: "Atualizar do provedor" }));
    await waitFor(() => expect(onListModels).toHaveBeenCalledTimes(1));
    await screen.findByRole("button", { name: "Atualizar do provedor" });

    // O que o provedor devolveu vem primeiro e marcado; as sugestões continuam disponíveis.
    await waitFor(() =>
      expect(optionGroups()).toEqual(["Do provedor", "Sugestões"]),
    );
    const values = optionValues();
    // Sem duplicar: cada identificador aparece uma única vez, mesmo sendo sugestão e resposta.
    expect(new Set(values).size).toBe(values.length);
    expect(values.filter((value) => value === "claude-sonnet-4-5")).toHaveLength(1);
    expect(values).toContain("claude-opus-4-5");
    expect(values).toContain("claude-haiku-4-5");
    // O nome legível do provedor prevalece sobre o rótulo da sugestão.
    expect(
      screen.getByRole("option", { name: "Claude Sonnet 4.5 (provedor)" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/modelos vieram do provedor/i)).toBeInTheDocument();
  });

  it("keeps the default list and explains why when the provider is unreachable", async () => {
    const { onListModels } = setup({ config: config() });
    onListModels.mockRejectedValue(
      new ApiError(
        502,
        "could not reach http://localhost:11434/v1/models: the connection was refused",
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Atualizar do provedor" }));

    // O motivo do servidor chega à tela junto com o aviso de que a lista padrão continua.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Não foi possível obter a lista de modelos do provedor.");
    expect(alert).toHaveTextContent(
      "Motivo informado pelo servidor: could not reach http://localhost:11434/v1/models: the connection was refused",
    );
    expect(alert).toHaveTextContent(/A lista do painel continua igual/i);
    // Nada da lista padrão mudou: continua sendo a lista, com o modelo gravado selecionado.
    const field = screen.getByLabelText("Modelo");
    expect(field.tagName).toBe("SELECT");
    expect(field).toHaveValue("claude-sonnet-4-5");
    expect(optionValues()).toEqual([
      ...MODEL_SUGGESTIONS.anthropic.map((suggestion) => suggestion.id),
      "__other__",
    ]);
    expect(screen.queryByRole("optgroup")).not.toBeInTheDocument();
  });

  it("still saves after a failed provider consultation", async () => {
    const { onSave, onListModels } = setup({}, [], new ApiError(409, "no stored key"));
    onListModels.mockRejectedValue(new ApiError(409, "the model provider has no stored key"));
    fireEvent.click(screen.getByRole("button", { name: "Atualizar do provedor" }));

    expect(
      await screen.findByText(/Não há chave para pedir a lista ao provedor/i),
    ).toBeInTheDocument();
    // A falha não fecha a porta: escolher na lista e salvar continua funcionando.
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "claude-opus-4-1" } });
    fireEvent.change(screen.getByLabelText("Chave da API"), {
      target: { value: "chave-ficticia" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cadastrar modelo" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave).model).toBe("claude-opus-4-1");
  });

  it("only shows the text field after 'outro (digitar)' and saves what was typed", async () => {
    const { onSave } = setup({ config: config() });
    expect(screen.getByLabelText("Modelo").tagName).toBe("SELECT");

    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "__other__" } });

    // A caixa de texto só aparece porque o usuário escolheu digitar, e começa no modelo atual.
    const typed = screen.getByLabelText("Modelo");
    expect(typed.tagName).toBe("INPUT");
    expect(typed).toHaveValue("claude-sonnet-4-5");
    fireEvent.change(typed, { target: { value: "modelo-local:7b" } });
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(submittedInput(onSave).model).toBe("modelo-local:7b");
  });

  it("keeps the saved model selectable when it is not in the catalog", () => {
    setup({ config: config({ model: "claude-que-nao-esta-na-lista" }) });

    // O campo é a lista, e o modelo gravado aparece nela: apagar seria pior que listar fora.
    const field = screen.getByLabelText("Modelo");
    expect(field.tagName).toBe("SELECT");
    expect(field).toHaveValue("claude-que-nao-esta-na-lista");
    expect(
      screen.getByRole("option", { name: "claude-que-nao-esta-na-lista" }),
    ).toBeInTheDocument();
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

  it("never puts the key in the text of the page", async () => {
    const { onListModels } = setup();
    fireEvent.change(screen.getByLabelText("Chave da API"), {
      target: { value: "chave-ficticia-de-teste" },
    });
    fireEvent.change(screen.getByLabelText("Modelo"), { target: { value: "claude-haiku-4-5" } });
    fireEvent.click(screen.getByRole("button", { name: "Atualizar do provedor" }));

    await waitFor(() => expect(onListModels).toHaveBeenCalledTimes(1));
    expect(document.body.textContent).not.toContain("chave-ficticia-de-teste");
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
    fireEvent.change(screen.getByLabelText("Modelo"), {
      target: { value: "claude-opus-4-1-latest" },
    });
    fireEvent.change(screen.getByLabelText("Chave da API"), { target: { value: "chave-ficticia" } });
    onSave.mockResolvedValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Salvar configuração" }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    // O que foi escolhido continua no formulário; a chave só sai quando a API confirma.
    expect(screen.getByLabelText("Modelo")).toHaveValue("claude-opus-4-1-latest");
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
    expect(screen.getAllByText("claude-sonnet-4-5").length).toBeGreaterThan(0);

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
