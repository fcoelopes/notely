import { useEffect, useState } from "react";
import type { AIModelConfig, AIModelConfigInput, AIModelProvider } from "../types";

const PROVIDER_LABELS: Record<AIModelProvider, string> = {
  anthropic: "Anthropic",
  openai_compatible: "Compatível com OpenAI",
};

const PROVIDER_HINTS: Record<AIModelProvider, string> = {
  anthropic: "Usa o endpoint oficial da Anthropic: a base URL não é pedida.",
  openai_compatible:
    "Vale para Ollama, LM Studio, vLLM e gateways compatíveis: a base URL é obrigatória.",
};

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
  onReload: () => void;
}

/**
 * Configuração do modelo de IA do dono, na barra lateral do Reader.
 *
 * O painel mostra o que a API devolve — provedor, modelo, base URL, se há chave — e nunca a
 * chave: o campo nasce vazio e, deixado vazio, mantém a chave já gravada, de modo que trocar
 * de modelo não obriga a redigitar o segredo. Os campos do formulário só são reescritos
 * quando a configuração confirmada muda, então uma falha da API preserva o que foi digitado.
 */
export function AIModelPanel({
  config,
  loading,
  busy,
  error,
  notice,
  onSave,
  onRemove,
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

  const hasKey = config?.has_key ?? false;
  const needsBaseUrl = provider === "openai_compatible";
  const missingBaseUrl = needsBaseUrl && !baseUrl.trim();
  const canSave = !busy && pending === null && Boolean(model.trim()) && !missingBaseUrl;

  // A configuração só muda aqui depois do sucesso da API. Sem configuração o formulário
  // volta vazio; com ela, reflete o que o servidor confirmou. A chave nunca é reescrita.
  useEffect(() => {
    setProvider(config?.provider ?? "anthropic");
    setModel(config?.model ?? "");
    setBaseUrl(config?.base_url ?? "");
    setEnabled(config?.enabled ?? true);
  }, [config]);

  const submit = async () => {
    if (!canSave) return;
    const input: AIModelConfigInput = {
      provider,
      model: model.trim(),
      // O endpoint oficial é do próprio provedor: não há base URL a guardar.
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
            <dt>Base URL</dt>
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
            onChange={(event) => setProvider(event.target.value as AIModelProvider)}
            value={provider}
          >
            <option value="anthropic">{PROVIDER_LABELS.anthropic}</option>
            <option value="openai_compatible">{PROVIDER_LABELS.openai_compatible}</option>
          </select>
          <small>{PROVIDER_HINTS[provider]}</small>
        </div>

        <div className="ai-config-field">
          <label htmlFor="ai-config-model">Modelo</label>
          <input
            aria-label="Modelo"
            id="ai-config-model"
            maxLength={200}
            onChange={(event) => setModel(event.target.value)}
            placeholder={needsBaseUrl ? "ex.: llama3.1" : "ex.: claude-sonnet-4-5"}
            value={model}
          />
        </div>

        {needsBaseUrl && (
          <div className="ai-config-field">
            <label htmlFor="ai-config-base-url">Base URL</label>
            <input
              aria-label="Base URL do provedor"
              id="ai-config-base-url"
              maxLength={500}
              onChange={(event) => setBaseUrl(event.target.value)}
              placeholder="ex.: http://localhost:11434/v1"
              value={baseUrl}
            />
            {missingBaseUrl ? (
              <small className="ai-config-required">
                Este provedor exige a base URL antes de gravar.
              </small>
            ) : (
              <small>A URL base da API compatível com OpenAI, com o caminho que ela expõe.</small>
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
