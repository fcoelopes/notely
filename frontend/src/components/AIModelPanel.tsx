import { useEffect, useState } from "react";
import { ApiError } from "../lib/api";
import type {
  AIModelConfig,
  AIModelConfigInput,
  AIModelListInput,
  AIModelOption,
  AIModelProvider,
} from "../types";

const PROVIDER_LABELS: Record<AIModelProvider, string> = {
  anthropic: "Anthropic",
  openai_compatible: "Compatível com OpenAI",
};

const PROVIDER_HINTS: Record<AIModelProvider, string> = {
  anthropic: "Usa o endpoint oficial da Anthropic: o endpoint não é pedido.",
  openai_compatible:
    "Vale para Ollama, LM Studio, vLLM e gateways compatíveis: informe a URL completa de /chat/completions.",
};

/** Valor da opção que devolve o campo de texto livre, para modelos fora da lista. */
const OTHER_MODEL = "__other__";
const OTHER_MODEL_LABEL = "outro (digitar)";

interface Props {
  config: AIModelConfig | null;
  /** A configuração ainda está sendo lida do servidor. */
  loading: boolean;
  /** Há uma gravação ou remoção em andamento. */
  busy: boolean;
  error: string | null;
  notice: string | null;
  /** Grava a configuração. `false` diz que a API falhou e os campos ficam preenchidos. */
  onSave: (input: AIModelConfigInput) => Promise<boolean>;
  /** Remove a configuração depois da confirmação no próprio painel. */
  onRemove: () => Promise<boolean>;
  /** Pede ao provedor a lista de modelos. A falha não impede salvar digitando. */
  onListModels: (input: AIModelListInput) => Promise<AIModelOption[]>;
  onReload: () => void;
}

/**
 * Mensagem da falha de listagem.
 *
 * O motivo vem do servidor — chave recusada pelo provedor, endpoint inalcançável, resposta
 * fora do formato de lista —, porque é o backend que falou com o provedor. Aqui ele só é
 * enquadrado em português: nada de "digite o nome" aqui, porque o campo continua sendo a
 * lista, com "outro (digitar)" para quem quiser escrever por decisão própria. A chave nunca
 * é repetida: o servidor não a devolve.
 */
const LISTING_LEADS: Record<number, string> = {
  409: "Não há chave para pedir a lista ao provedor.",
  422: "O pedido da lista de modelos foi recusado.",
};
const LISTING_LEAD = "Não foi possível obter a lista de modelos do provedor.";

function listingFailure(caught: unknown): string {
  if (!(caught instanceof ApiError)) return LISTING_LEAD;
  const lead = LISTING_LEADS[caught.status] ?? LISTING_LEAD;
  const reason = caught.message.trim();
  if (!reason) return lead;
  return `${lead} Motivo informado pelo servidor: ${reason}`;
}

/**
 * Configuração do modelo de IA do dono, na barra lateral do Reader.
 *
 * O painel mostra o que a API devolve — provedor, modelo, endpoint, se há chave — e nunca a
 * chave: o campo nasce vazio e, deixado vazio, mantém a chave já gravada, de modo que trocar
 * de modelo não obriga a redigitar o segredo. Os campos do formulário só são reescritos
 * quando a configuração confirmada muda, então uma falha da API preserva o que foi digitado.
 *
 * O modelo é escolhido na lista que o próprio provedor devolve. Com configuração salva a
 * lista é carregada automaticamente; sem ela, o botão explícito pede a chave e então lista.
 * Quando a lista não vem, o campo continua sendo uma lista — com o modelo atual e "outro
 * (digitar)" — e o motivo da falha aparece acima dele; a caixa de texto só aparece quando o
 * usuário escolhe "outro", de propósito.
 */
export function AIModelPanel({
  config,
  loading,
  busy,
  error,
  notice,
  onSave,
  onRemove,
  onListModels,
  onReload,
}: Props) {
  const [provider, setProvider] = useState<AIModelProvider>(config?.provider ?? "anthropic");
  const [model, setModel] = useState(config?.model ?? "");
  const [baseUrl, setBaseUrl] = useState(config?.base_url ?? "");
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [enabled, setEnabled] = useState(config?.enabled ?? true);
  const [confirmingRemoval, setConfirmingRemoval] = useState(false);
  const [pending, setPending] = useState<"save" | "remove" | null>(null);
  const [options, setOptions] = useState<AIModelOption[] | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(false);
  // O campo começa como texto porque ainda não há lista alguma para oferecer. Depois da
  // primeira listagem, quem manda é ela: só o usuário, escolhendo "outro (digitar)", sai
  // da lista — uma falha de rede não troca o campo por uma caixa de texto.
  const [typingModel, setTypingModel] = useState(!config);

  const hasKey = config?.has_key ?? false;
  const needsBaseUrl = provider === "openai_compatible";
  const missingBaseUrl = needsBaseUrl && !baseUrl.trim();
  const canSave = !busy && pending === null && Boolean(model.trim()) && !missingBaseUrl;
  const listed = options ?? [];
  const modelInList = listed.some((option) => option.id === model);
  // O campo é lista quando a lista veio do provedor ou quando a listagem falhou: nos dois
  // casos há escolha a oferecer, e o modelo atual aparece mesmo fora da lista devolvida.
  const showModelList = !typingModel && (listed.length > 0 || optionsError !== null);

  // A configuração só muda aqui depois do sucesso da API. Sem configuração o formulário
  // volta vazio; com ela, reflete o que o servidor confirmou. A chave nunca é reescrita.
  useEffect(() => {
    setProvider(config?.provider ?? "anthropic");
    setModel(config?.model ?? "");
    setBaseUrl(config?.base_url ?? "");
    setEnabled(config?.enabled ?? true);
  }, [config]);

  // Com configuração salva existe chave no servidor: a lista é pedida automaticamente,
  // com o provedor e o endpoint gravados. Sem configuração quem dispara é o botão, porque
  // a chave ainda precisa ser digitada.
  useEffect(() => {
    if (!config) {
      setOptions(null);
      setOptionsError(null);
      setLoadingOptions(false);
      setTypingModel(true);
      return;
    }
    let active = true;
    setLoadingOptions(true);
    setOptionsError(null);
    onListModels({ provider: config.provider, base_url: config.base_url })
      .then((loaded) => {
        if (!active) return;
        setOptions(loaded);
        // Modelo gravado que o provedor não devolve (endpoint local, versão nova) continua
        // no campo de texto: a lista não pode apagar o que já estava configurado.
        setTypingModel(!loaded.some((option) => option.id === config.model));
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setOptions(null);
        setOptionsError(listingFailure(caught));
        // O campo continua a lista: o modelo gravado segue selecionado nela e "outro
        // (digitar)" está ali para quem quiser escrever outro nome.
      })
      .finally(() => {
        if (active) setLoadingOptions(false);
      });
    return () => {
      active = false;
    };
  }, [config, onListModels]);

  // A lista pedida pelo botão usa o que está no formulário: a chave digitada vale só para
  // esta chamada, e o endpoint digitado decide a URL da lista.
  const loadModels = async () => {
    setLoadingOptions(true);
    setOptionsError(null);
    try {
      const loaded = await onListModels({
        provider,
        api_key: apiKey.trim() || undefined,
        base_url: needsBaseUrl ? baseUrl.trim() || null : null,
      });
      setOptions(loaded);
      // Um modelo já digitado que a lista não traz continua no campo de texto; sem modelo
      // escolhido, o campo passa a ser a lista.
      setTypingModel(model.trim() !== "" && !loaded.some((option) => option.id === model));
    } catch (caught) {
      setOptions(null);
      setOptionsError(listingFailure(caught));
      // Sem lista, o campo continua sendo a lista, com o modelo atual: trocá-lo por caixa
      // de texto esconderia a escolha que o painel oferece.
    } finally {
      setLoadingOptions(false);
    }
  };

  const submit = async () => {
    if (!canSave) return;
    const input: AIModelConfigInput = {
      provider,
      model: model.trim(),
      // O endpoint oficial é do próprio provedor: não há endpoint a guardar.
      base_url: needsBaseUrl ? baseUrl.trim() : null,
      enabled,
    };
    const trimmedKey = apiKey.trim();
    // Sem chave digitada, a que está gravada é mantida pela API.
    if (trimmedKey) input.api_key = trimmedKey;
    setPending("save");
    let saved = false;
    try {
      saved = await onSave(input);
    } finally {
      setPending(null);
    }
    if (saved) {
      // A chave não fica no estado da tela depois de gravada.
      setApiKey("");
      setShowKey(false);
    }
  };

  return (
    <section className="ai-config-panel">
      <p className="eyebrow">Configuração do modelo</p>

      {loading && !config ? (
        <p className="ai-config-loading">Lendo a configuração…</p>
      ) : config ? (
        <dl className="ai-config-current">
          <div>
            <dt>Provedor</dt>
            <dd>{PROVIDER_LABELS[config.provider]}</dd>
          </div>
          <div>
            <dt>Modelo</dt>
            <dd>{config.model}</dd>
          </div>
          <div>
            <dt>Endpoint</dt>
            <dd>{config.base_url ?? "endpoint oficial do provedor"}</dd>
          </div>
          <div>
            <dt>Chave</dt>
            <dd>{config.has_key ? "gravada no servidor" : "não cadastrada"}</dd>
          </div>
          <div>
            <dt>Estado</dt>
            <dd>{config.enabled ? "ativo" : "desativado"}</dd>
          </div>
        </dl>
      ) : (
        <div className="panel-empty panel-empty--compact">
          <p>Nenhum modelo de IA configurado.</p>
          <small>
            Cadastre o provedor e a chave para usar o assistente e a extração de conceitos.
            A chave fica cifrada no servidor e não volta para esta tela.
          </small>
        </div>
      )}

      {error && (
        <p className="panel-error" role="alert">
          {error}
          <button type="button" onClick={onReload}>
            Atualizar
          </button>
        </p>
      )}
      {notice && (
        <p className="ai-config-notice" role="status">
          {notice}
        </p>
      )}

      <form
        className="ai-config-form"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="ai-config-field">
          <label htmlFor="ai-config-provider">Provedor</label>
          <select
            aria-label="Provedor do modelo"
            id="ai-config-provider"
            onChange={(event) => {
              setProvider(event.target.value as AIModelProvider);
              // A lista é do provedor anterior: some até ser pedida de novo.
              setOptions(null);
              setOptionsError(null);
            }}
            value={provider}
          >
            <option value="anthropic">{PROVIDER_LABELS.anthropic}</option>
            <option value="openai_compatible">{PROVIDER_LABELS.openai_compatible}</option>
          </select>
          <small>{PROVIDER_HINTS[provider]}</small>
        </div>

        <div className="ai-config-field">
          <label htmlFor="ai-config-model">Modelo</label>
          {/* O motivo da falha fica acima do campo: é o que explica a lista incompleta. */}
          {optionsError && (
            <p className="ai-config-list-error" role="alert">
              {optionsError}
            </p>
          )}
          {showModelList ? (
            <select
              aria-label="Modelo"
              id="ai-config-model"
              onChange={(event) => {
                const chosen = event.target.value;
                if (chosen === OTHER_MODEL) {
                  // O texto livre começa com o modelo atual: nada do que foi escolhido se perde.
                  setTypingModel(true);
                  return;
                }
                setModel(chosen);
              }}
              value={model}
            >
              {model === "" && <option value="">Escolha um modelo</option>}
              {/* Modelo atual que a lista não traz continua selecionado, em vez de sumir. */}
              {model !== "" && !modelInList && <option value={model}>{model}</option>}
              {listed.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.display_name ?? option.id}
                </option>
              ))}
              <option value={OTHER_MODEL}>{OTHER_MODEL_LABEL}</option>
            </select>
          ) : (
            <input
              aria-label="Modelo"
              id="ai-config-model"
              maxLength={200}
              onChange={(event) => setModel(event.target.value)}
              placeholder={needsBaseUrl ? "ex.: llama3.1" : "ex.: claude-sonnet-4-5"}
              value={model}
            />
          )}
          <div className="ai-config-model-actions">
            <button
              className="ai-config-list"
              disabled={loadingOptions || busy || pending !== null}
              onClick={() => void loadModels()}
              type="button"
            >
              {loadingOptions ? "Carregando…" : "Carregar modelos"}
            </button>
            {listOptionsHint(showModelList, listed.length)}
          </div>
        </div>

        {needsBaseUrl && (
          <div className="ai-config-field">
            <label htmlFor="ai-config-base-url">Endpoint de /chat/completions</label>
            <input
              aria-label="Endpoint do provedor em /chat/completions"
              id="ai-config-base-url"
              maxLength={500}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="ex.: http://localhost:11434/v1/chat/completions"
              value={baseUrl}
            />
            {missingBaseUrl ? (
              <small className="ai-config-required">
                Este provedor exige a URL de /chat/completions antes de gravar.
              </small>
            ) : (
              <small>
                A URL completa de /chat/completions do provedor, com o caminho que ele expõe:
                é ela que o assistente chama e dela sai a lista de modelos.
              </small>
            )}
          </div>
        )}

        <div className="ai-config-field">
          <label htmlFor="ai-config-api-key">Chave da API</label>
          <div className="ai-config-key">
            <input
              aria-label="Chave da API"
              autoComplete="off"
              id="ai-config-api-key"
              maxLength={400}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={hasKey ? "deixe em branco para manter" : "cole a chave do provedor"}
              spellCheck={false}
              type={showKey ? "text" : "password"}
              value={apiKey}
            />
            <button
              aria-controls="ai-config-api-key"
              aria-pressed={showKey}
              className="ai-config-reveal"
              onClick={() => setShowKey((current) => !current)}
              type="button"
            >
              {showKey ? "Ocultar" : "Mostrar"}
            </button>
          </div>
          <small>
            {hasKey
              ? "Deixe em branco para manter a chave já gravada: ela não é exibida de novo."
              : "Sem chave o modelo não responde: ela é cifrada no servidor e nunca volta para esta tela."}
          </small>
        </div>

        <label className="ai-config-toggle">
          <input
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
            type="checkbox"
          />
          <span>Usar este modelo no assistente e na extração de conceitos</span>
        </label>

        <div className="ai-config-actions">
          <button className="ai-config-save" disabled={!canSave} type="submit">
            {pending === "save"
              ? "Gravando…"
              : config
                ? "Salvar configuração"
                : "Cadastrar modelo"}
          </button>
        </div>
      </form>

      {config &&
        (confirmingRemoval ? (
          <div className="ai-config-confirm" role="group" aria-label="Confirmar remoção">
            <p>
              Remover apaga a chave gravada e desliga o assistente e a extração de conceitos
              até você configurar de novo.
            </p>
            <div className="ai-config-actions">
              <button
                className="ai-config-danger"
                disabled={busy || pending !== null}
                onClick={() => {
                  void (async () => {
                    setPending("remove");
                    let removed = false;
                    try {
                      removed = await onRemove();
                    } finally {
                      setPending(null);
                    }
                    if (removed) setConfirmingRemoval(false);
                  })();
                }}
                type="button"
              >
                {pending === "remove" ? "Removendo…" : "Confirmar remoção"}
              </button>
              <button
                className="ai-config-cancel"
                disabled={busy || pending !== null}
                onClick={() => setConfirmingRemoval(false)}
                type="button"
              >
                Cancelar
              </button>
            </div>
          </div>
        ) : (
          <button
            className="ai-config-remove"
            disabled={busy || pending !== null}
            onClick={() => setConfirmingRemoval(true)}
            type="button"
          >
            Remover configuração
          </button>
        ))}
    </section>
  );
}

/** Diz de onde veio o que está no campo, para a lista não parecer uma caixa qualquer. */
function listOptionsHint(showModelList: boolean, count: number) {
  if (showModelList && count > 0) {
    return (
      <small className="ai-config-list-hint">
        {count} modelos devolvidos pelo provedor. Para um modelo que não está na lista,
        escolha &quot;{OTHER_MODEL_LABEL}&quot;.
      </small>
    );
  }
  if (showModelList) {
    return (
      <small className="ai-config-list-hint">
        A lista não veio, mas o campo continua sendo a escolha: o modelo atual segue
        selecionado e &quot;{OTHER_MODEL_LABEL}&quot; abre o nome escrito à mão.
      </small>
    );
  }
  return (
    <small className="ai-config-list-hint">
      Digite o nome do modelo ou carregue a lista do provedor.
    </small>
  );
}
