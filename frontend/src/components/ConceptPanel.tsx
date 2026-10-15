import { useState } from "react";
import type { ConceptOverview } from "../types";

interface Props {
  concepts: ConceptOverview[];
  /** Documentos da sessão aberta: é por eles que a lista é filtrada. */
  documentIds: string[];
  busy: boolean;
  error: string | null;
  onReload: () => void;
  onAssociate: (sourceConceptId: string, targetConceptId: string) => void;
  onRemoveAssociation: (relationId: string) => void;
}

interface AssociationRow {
  relationId: string;
  sourceId: string;
  targetId: string;
}

/**
 * Conceitos das obras da sessão, com o lastro de cada um.
 *
 * O que aparece aqui é inferência de modelo ou heurística de frequência, nunca conhecimento
 * confirmado, e a tela diz isso: nada é promovido a autoral sem o gesto da pessoa. O que é
 * autoral é a associação criada entre dois conceitos, e ela é removível.
 */
export function ConceptPanel({
  concepts,
  documentIds,
  busy,
  error,
  onReload,
  onAssociate,
  onRemoveAssociation,
}: Props) {
  const [selected, setSelected] = useState<string[]>([]);

  const names = new Map(concepts.map((concept) => [concept.id, concept.name]));
  const associations: AssociationRow[] = [];
  const seen = new Set<string>();
  concepts.forEach((concept) => {
    concept.associations.forEach((association) => {
      if (seen.has(association.relation_id)) return;
      seen.add(association.relation_id);
      associations.push({
        relationId: association.relation_id,
        sourceId: association.source_concept_id,
        targetId: association.target_concept_id,
      });
    });
  });

  const [sourceId, targetId] = selected;
  const source = concepts.find((concept) => concept.id === sourceId) ?? null;
  const target = concepts.find((concept) => concept.id === targetId) ?? null;
  // A aresta já existente é por par ordenado: reaproveitá-la é o que evita duplicar.
  const alreadyRelated =
    source !== null &&
    target !== null &&
    source.associations.some(
      (association) =>
        association.outgoing && association.other_concept_id === target.id,
    );

  const toggle = (conceptId: string) => {
    setSelected((current) => {
      if (current.includes(conceptId)) return current.filter((id) => id !== conceptId);
      // Só dois por vez: o terceiro substitui o primeiro, e a ordem de escolha é a ordem da
      // associação, que é significativa hoje.
      return [...current, conceptId].slice(-2);
    });
  };

  return (
    <section className="concept-panel">
      <div className="claim-heading">
        <p className="eyebrow">Conceitos das obras desta sessão</p>
        <span className="claim-count">{concepts.length}</span>
      </div>

      <p className="concept-warning">
        Extraídos por modelo ou heurística de frequência: são <strong>inferência</strong>,
        não conhecimento confirmado, e podem estar errados. A associação que você cria entre
        dois deles é sua.
      </p>

      {error && (
        <p className="panel-error" role="alert">
          {error}
          <button type="button" onClick={onReload}>
            Atualizar
          </button>
        </p>
      )}

      {concepts.length === 0 ? (
        <div className="panel-empty panel-empty--compact">
          <p>Nenhum conceito ainda.</p>
          <small>
            Traga um PDF para a sessão e anote um trecho: os conceitos aparecem depois da
            extração, ligados às suas anotações.
          </small>
        </div>
      ) : (
        <>
          <ol className="concept-list">
            {concepts.map((concept) => {
              const inSession = concept.documents.filter((document) =>
                documentIds.includes(document.document_id),
              );
              const sessionCount = inSession.reduce(
                (total, document) => total + document.annotation_count,
                0,
              );
              const isSelected = selected.includes(concept.id);
              return (
                <li key={concept.id}>
                  <article className="concept-card">
                    <div className="concept-heading">
                      <strong>{concept.name}</strong>
                      <button
                        aria-pressed={isSelected}
                        className={`concept-select ${isSelected ? "is-selected" : ""}`}
                        onClick={() => toggle(concept.id)}
                        type="button"
                      >
                        {isSelected ? "Selecionado" : "Selecionar"}
                      </button>
                    </div>
                    <small>
                      {concept.annotation_count === 1
                        ? "1 trecho"
                        : `${concept.annotation_count} trechos`}
                      {sessionCount !== concept.annotation_count &&
                        ` · ${sessionCount} nesta sessão`}
                    </small>
                    {inSession.length > 0 && (
                      <p className="concept-documents">
                        {inSession.map((document) => document.document_title).join(" · ")}
                      </p>
                    )}
                  </article>
                </li>
              );
            })}
          </ol>

          {source && target && (
            <div className="concept-associate">
              <p>
                Associar <strong>{source.name}</strong> → <strong>{target.name}</strong>
              </p>
              <small>
                A ordem do par é significativa hoje: associar na ordem inversa cria outra
                associação.
              </small>
              {alreadyRelated ? (
                <small className="concept-already">
                  Estes dois conceitos já estão associados nesta ordem.
                </small>
              ) : (
                <button
                  className="concept-associate-action"
                  disabled={busy}
                  onClick={() => onAssociate(source.id, target.id)}
                  type="button"
                >
                  Associar conceitos
                </button>
              )}
            </div>
          )}

          <div className="concept-associations">
            <p className="eyebrow">Associações existentes</p>
            {associations.length === 0 ? (
              <small>Nenhuma associação ainda. Selecione dois conceitos acima.</small>
            ) : (
              <ul>
                {associations.map((association) => (
                  <li key={association.relationId}>
                    <span>
                      {names.get(association.sourceId) ?? "conceito removido"} →{" "}
                      {names.get(association.targetId) ?? "conceito removido"}
                    </span>
                    <button
                      aria-label="Remover associação"
                      className="claim-remove"
                      disabled={busy}
                      onClick={() => onRemoveAssociation(association.relationId)}
                      type="button"
                    >
                      Remover
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </section>
  );
}
