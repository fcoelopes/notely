import type { ClaimEvidence, ClaimEvidenceRelation } from "../types";

interface Props {
  claims: ClaimEvidence[];
  busy: boolean;
  error: string | null;
  onReload: () => void;
  onRemove: (claimId: string) => void;
}

function relationLabel(relation: ClaimEvidenceRelation): string {
  return relation === "contradicts" ? "contradiz este trecho" : "sustenta este trecho";
}

/**
 * Afirmações das obras da sessão, com a evidência ao lado.
 *
 * A afirmação sem evidência continua listada e marcada: o domínio garante que ela segue
 * sendo do usuário depois que a anotação que a sustentava é removida, e esconder isso
 * apagaria conhecimento autoral da leitura.
 */
export function ClaimPanel({ claims, busy, error, onReload, onRemove }: Props) {
  return (
    <section className="claim-panel">
      <div className="claim-heading">
        <p className="eyebrow">O que você afirma</p>
        <span className="claim-count">{claims.length}</span>
      </div>

      {error && (
        <p className="panel-error" role="alert">
          {error}
          <button type="button" onClick={onReload}>
            Atualizar
          </button>
        </p>
      )}

      {claims.length === 0 ? (
        <div className="panel-empty panel-empty--compact">
          <p>Nenhuma afirmação ainda.</p>
          <small>
            No caderno de anotações, escolha um trecho e use Afirmar para dizer o que ele
            sustenta ou contradiz. A afirmação fica ligada ao trecho que serviu de evidência.
          </small>
        </div>
      ) : (
        <ol className="claim-list">
          {claims.map((entry) => (
            <li key={`${entry.claim.id}-${entry.relation_id ?? "sem-evidencia"}`}>
              <article className="claim-card">
                <p className="claim-text">{entry.claim.text}</p>
                {entry.relation ? (
                  <>
                    <span className={`claim-relation claim-relation--${entry.relation}`}>
                      {relationLabel(entry.relation)}
                    </span>
                    <p className="claim-evidence">
                      <span>Evidência</span> {entry.document_title ?? "documento indisponível"}
                      {entry.page_number !== null && ` · página ${entry.page_number}`}
                    </p>
                    {entry.quote && <q>{entry.quote}</q>}
                  </>
                ) : (
                  <>
                    <span className="claim-relation claim-relation--none">sem evidência</span>
                    <p className="claim-evidence-missing">
                      A anotação que sustentava esta afirmação não está mais no acervo. Ela
                      continua sendo sua e segue listada aqui.
                    </p>
                  </>
                )}
                <button
                  aria-label={`Remover a afirmação ${entry.claim.text}`}
                  className="claim-remove"
                  disabled={busy}
                  onClick={() => onRemove(entry.claim.id)}
                  type="button"
                >
                  Remover afirmação
                </button>
              </article>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
