import { useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";
import { MODEL_SUGGESTIONS, mergeModelChoices } from "../lib/modelCatalog";
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

/** Nome do grupo de opções que o provedor confirmou, para separar das sugestões locais. */
const PROVIDER_GROUP_LABEL = "Do provedor";
const SUGGESTION_GROUP_LABEL = "Sugestões";

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
  /**
   * Consulta opcional ao provedor. A lista padrão do painel não depende dela: falhar aqui não
   * muda o que está no campo nem impede escolher e salvar.
   */
  onListModels: (input: AIModelListInput) => Promise<AIModelOption[]>;
  onReload: () => void;
}

/**
 * Mensagem da falha da consulta ao provedor.
 *
 * O motivo vem do servidor — chave recusada pelo provedor, endpoint inalcançável, resposta
 * fora do formato de lista —, porque é o backend que falou com o provedor. Aqui ele só é
 * enquadrado em português, e sempre depois de dizer que a lista padrão continua valendo: a
 * falha é da consulta opcional, não da escolha. A chave nunca é repetida: o servidor não a
 * devolve.
 */
const LISTING_LEADS: Record<number, string> = {
  409: "Não há chave para pedir a lista ao provedor.",
  422: "O pedido da lista de modelos foi recusado.",
};
const LISTING_LEAD = "Não foi possível obter a lista de modelos do provedor.";
const LISTING_KEEPS = "A lista do painel continua igual: escolher e salvar não dependem disso.";

function listingFailure(caught: unknown): string {
  if (!(caught instanceof ApiError)) return `${LISTING_LEAD} ${LISTING_KEEPS}`;
  const lead = LISTING_LEADS[caught.status] ?? LISTING_LEAD;
  const reason = caught.message.trim();
  if (!reason) return `${lead} ${LISTING_KEEPS}`;
  return `${lead} Motivo informado pelo servidor: ${reason} ${LISTING_KEEPS}`;
}

/**
 * Configuração do modelo de IA do dono, na barra lateral do Reader.
 *
 * A ordem do formulário é provedor, modelo, chave, salvar, porque a chave serve para
 * conectar, não para listar: ao escolher o provedor a lista de modelos já está ali, vinda do
 * catálogo local de sugestões, sem chamada nenhuma e sem exigir chave. A consulta ao provedor
 * — "Atualizar do provedor" — é opcional, usa a chave digitada ou a gravada e apenas mescla o
 * que voltar na lista; se falhar, a lista padrão continua intacta e a escolha não é afetada.
 *
 * O painel mostra o que a API devolve — provedor, modelo, endpoint, se há chave — e nunca a
 * chave: o campo nasce vazio e, deixado vazio, mantém a chave já gravada, de modo que trocar
 * de modelo não obriga a redigitar o segredo. Os campos do formulário só são reescritos
 * quando a configuração confirmada muda, então uma falha da API preserva o que foi digitado.
 *
 * O campo de modelo é sempre uma lista; a caixa de texto só aparece quando o usuário escolhe
 * "outro (digitar)", de propósito.
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
  /** O que o provedor devolveu na última consulta, para o provedor escolhido. */
  const [remoteOptions, setRemoteOptions] = useState<AIModelOption[]>([]);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(false);
  // A caixa de texto é exceção: só aparece porque o usuário escolheu "outro (digitar)".
  const [typingModel, setTypingModel] = useState(false);
  // Descarta a resposta de uma consulta que ficou para trás quando o provedor mudou no meio.
  const listRequest = useRef(0);

  const hasKey = config?.has_key ?? false;
  const needsBaseUrl = provider === "openai_compatible";
  const missingBaseUrl = needsBaseUrl && !baseUrl.trim();
  const canSave = !busy && pending === null && Boolean(model.trim()) && !missingBaseUrl;
  const choices = mergeModelChoices(provider, remoteOptions);
  const providerChoices = choices.filter((choice) => choice.origin === "provedor");
  const suggestionChoices = choices.filter((choice) => choice.origin === "sugestao");
  const modelInList = choices.some((choice) => choice.id === model);

  // A configuração só muda aqui depois do sucesso da API. Sem configuração o formulário
  // volta vazio; com ela, reflete o que o servidor confirmou. A chave nunca é reescrita, e a
  // lista que veio do provedor é descartada: ela é do provedor e do modelo anteriores.
  useEffect(() => {
    setProvider(config?.provider ?? "anthropic");
    setModel(config?.model ?? "");
    setBaseUrl(config?.base_url ?? "");
    setEnabled(config?.enabled ?? true);
    setRemoteOptions([]);
    setOptionsError(null);
    setLoadingOptions(false);
    setTypingModel(false);
    listRequest.current += 1;
  }, [config]);

  /** Troca o provedor e, com ele, a lista: o modelo do provedor anterior não vale aqui. */
  const changeProvider = (next: AIModelProvider) => {
    listRequest.current += 1;
    setProvider(next);
    setRemoteOptions([]);
    setOptionsError(null);
    setLoadingOptions(false);
    setTypingModel(false);
    setModel((current) => {
      if (MODEL_SUGGESTIONS[next].some((suggestion) => suggestion.id === current)) return current;
      // Voltar ao provedor da configuração devolve o modelo gravado, mesmo fora do catálogo; o
      // que é de outro provedor sai do campo, em vez de ser gravado no lugar errado.
      if (next === config?.provider && !current.trim()) return config?.model ?? "";
      return "";
    });
  };

  /**
   * Consulta opcional ao provedor. A chave digitada vale só para esta chamada; sem ela, o
   * servidor usa a que está gravada. O endpoint digitado decide a URL da consulta.
   */
  const refreshFromProvider = async () => {
    const request = listRequest.current + 1;
    listRequest.current = request;
    setLoadingOptions(true);
    setOptionsError(null);
    try {
      const loaded = await onListModels({
        provider,
        api_key: apiKey.trim() || undefined,
        base_url: needsBaseUrl ? baseUrl.trim() || null : null,
      });
      if (request !== listRequest.current) return;
      // Mescla com o catálogo local: o que o provedor devolveu confirma, corrige e acrescenta.
      setRemoteOptions(loaded);
    } catch (caught) {
      if (request !== listRequest.current) return;
      // A lista padrão não é tocada: a falha é da consulta, não da escolha.
      setOptionsError(listingFailure(caught));
    } finally {
      if (request === listRequest.current) setLoadingOptions(false);
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
            Escolha o provedor, escolha o modelo na lista e informe a chave. A chave fica
            cifrada no servidor e não volta para esta tela.
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
            onChange={(event) => changeProvider(event.target.value as AIModelProvider)}
            value={provider}
          >
            <option value="anthropic">{PROVIDER_LABELS.anthropic}</option>
            <option value="openai_compatible">{PROVIDER_LABELS.openai_compatible}</option>
          </select>
          <small>{PROVIDER_HINTS[provider]}</small>
        </div>

        <div className="ai-config-field">
          <label htmlFor="ai-config-model">Modelo</label>
          {/* O motivo da falha fica acima do campo: é o que explica a consulta que não veio. */}
          {optionsError && (
            <p className="ai-config-list-error" role="alert">
              {optionsError}
            </p>
          )}
          {typingModel ? (
            <input
              aria-label="Modelo"
              id="ai-config-model"
              maxLength={200}
              onChange={(event) => setModel(event.target.value)}
              placeholder={needsBaseUrl ? "ex.: llama3.1" : "ex.: claude-sonnet-4-5"}
              value={model}
            />
          ) : (
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
              {providerChoices.length > 0 && (
                <optgroup label={PROVIDER_GROUP_LABEL}>
                  {providerChoices.map((choice) => (
                    <option key={choice.id} value={choice.id}>
                      {choice.label}
                    </option>
                  ))}
                </optgroup>
              )}
              {suggestionChoices.length > 0 && (
                <optgroup label={SUGGESTION_GROUP_LABEL}>
                  {suggestionChoices.map((choice) => (
                    <option key={choice.id} value={choice.id}>
                      {choice.label}
                    </option>
                  ))}
                </optgroup>
              )}
              <option value={OTHER_MODEL}>{OTHER_MODEL_LABEL}</option>
            </select>
          )}
          <div className="ai-config-model-actions">
            <button
              className="ai-config-list"
              disabled={loadingOptions || busy || pending !== null}
              onClick={() => void refreshFromProvider()}
              type="button"
            >
              {loadingOptions ? "Consultando o provedor…" : "Atualizar do provedor"}
            </button>
            {listOptionsHint(providerChoices.length)}
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
                é ela que o assistente chama e é dela que sai a consulta de modelos.
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
          {/* A chave conecta; ela não é condição para a lista acima existir. */}
          <small>A chave serve para conectar: a lista de modelos acima não depende dela.</small>
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

/** Diz de onde veio o que está no campo e que a consulta ao provedor é opcional. */
function listOptionsHint(providerCount: number) {
  if (providerCount > 0) {
    return (
      <small className="ai-config-list-hint">
        {providerCount === 1
          ? "1 modelo veio do provedor."
          : `${providerCount} modelos vieram do provedor.`}{" "}
        As opções em &quot;{SUGGESTION_GROUP_LABEL}&quot; são sugestões do painel e podem
        estar desatualizadas. Para um modelo que não está na lista, escolha &quot;
        {OTHER_MODEL_LABEL}&quot;.
      </small>
    );
  }
  return (
    <small className="ai-config-list-hint">
      A lista é uma sugestão do painel e pode estar desatualizada; consultar o provedor é
      opcional e usa a chave digitada ou a gravada. Para um modelo fora da lista, escolha
      &quot;{OTHER_MODEL_LABEL}&quot;.
    </small>
  );
}
