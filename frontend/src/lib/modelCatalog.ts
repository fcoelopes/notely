import type { AIModelOption, AIModelProvider } from "../types";

/** Uma sugestão de modelo: `id` é o que se grava na configuração, `label` é o que se lê. */
export interface ModelSuggestion {
  readonly id: string;
  readonly label: string;
}

/**
 * Sugestões de modelo por provedor, para a tela oferecer escolha antes de qualquer chamada.
 *
 * Isto é **sugestão**, não catálogo: envelhece sem aviso e nada aqui é validado contra o
 * provedor. A lista autoritativa é a que o próprio provedor devolve em
 * `POST /api/ai-config/models` — é ela que confirma, corrige e acrescenta identificadores, e
 * por isso "Atualizar do provedor" existe. Quem precisar de um modelo fora desta lista usa
 * "outro (digitar)"; a tela nunca impede salvar por causa do que está aqui.
 *
 * Ao atualizar, prefira identificadores que o provedor mantém como alias estável, em vez de
 * datas de lançamento que saem do ar.
 */
export const MODEL_SUGGESTIONS: Record<AIModelProvider, readonly ModelSuggestion[]> = {
  anthropic: [
    { id: "claude-sonnet-4-5", label: "Claude Sonnet 4.5" },
    { id: "claude-sonnet-4-5-latest", label: "Claude Sonnet 4.5 (latest)" },
    { id: "claude-opus-4-1", label: "Claude Opus 4.1" },
    { id: "claude-opus-4-1-latest", label: "Claude Opus 4.1 (latest)" },
    { id: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
    { id: "claude-haiku-4-5-latest", label: "Claude Haiku 4.5 (latest)" },
  ],
  // Cobre a OpenAI e os endpoints compatíveis. Um servidor local (Ollama, LM Studio, vLLM)
  // quase nunca tem estes identificadores: para ele, o caminho é "outro (digitar)" ou a
  // consulta ao próprio endpoint.
  openai_compatible: [
    { id: "gpt-4o", label: "GPT-4o" },
    { id: "gpt-4o-mini", label: "GPT-4o mini" },
    { id: "gpt-4.1", label: "GPT-4.1" },
    { id: "gpt-4.1-mini", label: "GPT-4.1 mini" },
  ],
};

/** De onde veio a opção que está na lista: do catálogo local ou da resposta do provedor. */
export type ModelChoiceOrigin = "sugestao" | "provedor";

export interface ModelChoice {
  id: string;
  label: string;
  origin: ModelChoiceOrigin;
}

/**
 * Soma as sugestões do provedor escolhido às opções que ele devolveu, sem duplicar.
 *
 * As sugestões vêm primeiro, na ordem do catálogo, e o que o provedor devolveu de novo entra
 * depois, na ordem dele. Um identificador que é sugestão **e** veio do provedor passa a
 * contar como vindo do provedor: a resposta dele é a fonte autoritativa, inclusive para o
 * nome legível. Sem lista do provedor (`remote` vazio), o resultado são só as sugestões.
 */
export function mergeModelChoices(
  provider: AIModelProvider,
  remote: readonly AIModelOption[] = [],
): ModelChoice[] {
  const choices: ModelChoice[] = MODEL_SUGGESTIONS[provider].map((suggestion) => ({
    id: suggestion.id,
    label: suggestion.label,
    origin: "sugestao",
  }));
  const byId = new Map(choices.map((choice) => [choice.id, choice]));

  for (const option of remote) {
    const id = option.id.trim();
    if (!id) continue;
    const label = option.display_name?.trim() || id;
    const known = byId.get(id);
    if (known) {
      known.origin = "provedor";
      if (option.display_name?.trim()) known.label = label;
      continue;
    }
    const added: ModelChoice = { id, label, origin: "provedor" };
    choices.push(added);
    byId.set(id, added);
  }

  return choices;
}
