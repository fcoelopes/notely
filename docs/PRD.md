# PRD — Notely

**Versão:** 0.3  
**Status:** Draft  
**Produto:** Notely  
**Componente principal:** Notely Reader

## 1. Visão

O Notely será um leitor de documentos voltado para leitura técnica, estudo e construção de conhecimento pessoal.

O usuário continua sendo o agente principal: lê, decide o que é relevante, destaca, comenta, registra dúvidas e cria relações. O Notely persiste essas ações, preserva sua proveniência, enriquece o material em segundo plano e constrói um grafo de conhecimento navegável.

O objetivo não é substituir a leitura humana. A inteligência do sistema deve acompanhar a leitura e reduzir o trabalho de organizar, reencontrar e relacionar o que foi estudado.

## 2. Problema

A leitura técnica em PDF costuma deixar conhecimento fragmentado:

- highlights ficam presos a um arquivo ou leitor específico;
- comentários e dúvidas não se conectam ao que já foi estudado;
- figuras, tabelas, diagramas e equações exigem copiar conteúdo para outras ferramentas;
- pesquisas externas se perdem fora do contexto que as originou;
- anotações antigas são difíceis de reencontrar;
- um mesmo conceito pode aparecer em muitos documentos sem que o usuário perceba a relação;
- ferramentas tradicionais de RAG tendem a tratar o documento inteiro como fonte, sem distinguir o que chamou a atenção do usuário.

O Notely deve transformar o processo de leitura em memória estruturada sem adicionar burocracia ao ato de ler.

## 3. Princípio central

> Eu leio. Eu decido o que é relevante. Eu anoto. O Notely guarda, relaciona e me ajuda quando eu pedir.

A IA não deve pré-ler ou resumir automaticamente todos os documentos por padrão.

## 4. Independência

O Notely é um produto independente.

Ele poderá reutilizar ideias e infraestrutura semelhantes às existentes no Clarc, como PostgreSQL, TimescaleDB, Apache AGE, web search e providers de modelos, mas nenhuma capacidade essencial do Notely deve depender do Clarc estar ativo.

Integração futura com o Clarc deve ocorrer por contrato de API ou eventos.

## 5. Objetivos do MVP

O primeiro MVP deve permitir:

1. abrir e navegar por PDFs;
2. criar highlights;
3. adicionar comentários;
4. registrar dúvidas;
5. marcar algo como importante ou discordância;
6. persistir anotações imediatamente;
7. reconstruir as anotações ao reabrir o documento;
8. importar annotations nativas de PDFs já marcados;
9. preservar documento, página, posição, trecho e autoria;
10. registrar eventos de leitura;
11. gerar embeddings e busca semântica em segundo plano;
12. projetar conceitos e relações em grafo;
13. permitir assistência multimodal sobre regiões selecionadas;
14. manter a base preparada para pesquisa externa sob demanda.

## 6. Não objetivos do MVP

O MVP não deve:

- resumir automaticamente todo PDF aberto;
- interromper a leitura a cada highlight;
- construir relações em massa sem vínculo com a atividade do usuário;
- virar um gerenciador bibliográfico completo;
- depender de Neo4j;
- depender do Clarc;
- enviar o PDF inteiro para um serviço externo por padrão;
- bloquear a leitura aguardando embeddings, grafo ou IA.

## 7. Ações de leitura

| Ação | Significado primário | Comportamento esperado |
|---|---|---|
| Highlight | Isto é relevante | Persistir e enriquecer silenciosamente |
| Comentário | Minha interpretação | Persistir como nota do usuário |
| Dúvida | Preciso entender | Abrir assistência contextual sob demanda |
| Importante | Alta relevância | Aumentar prioridade de recuperação |
| Discordo | Contestação | Registrar discordância explícita |
| Relacionar | Conexão intencional | Criar relação explícita |
| Perguntar ao Notely | Quero ajuda agora | Usar trecho, página, imagem e conhecimento existente como contexto |

A intenção original do usuário nunca deve ser reclassificada silenciosamente por um modelo.

## 8. Notely Reader

O Reader será o núcleo da experiência.

A interface deve priorizar o documento e oferecer, sem poluição visual:

- navegação por páginas;
- zoom e busca;
- seleção de texto;
- highlight;
- nota;
- dúvida;
- importante;
- discordância;
- relação explícita;
- acesso às anotações;
- painel contextual quando o usuário pedir ajuda.

Ações que não pedem resposta devem permanecer silenciosas. Painéis de IA não devem abrir automaticamente para cada marcação.

## 9. PDFs já anotados

O Notely deve suportar três situações:

1. annotation nativa no PDF: extrair diretamente;
2. highlight achatado no conteúdo visual: detectar com visão quando necessário;
3. PDF escaneado: usar OCR/multimodal como fallback.

Depois de normalizadas, anotações antigas e novas devem usar o mesmo modelo de dados.

## 10. Multimodal

O multimodal é uma capacidade central, não apenas um fallback de OCR.

Casos prioritários:

- gráficos;
- tabelas;
- diagramas;
- equações;
- figuras;
- páginas escaneadas;
- regiões selecionadas;
- highlights achatados;
- perguntas que dependam de layout visual.

O sistema deve enviar ao modelo o menor contexto suficiente: crop da região, texto próximo, legenda, pergunta e, quando necessário, alguns conceitos relevantes.

O provider multimodal deve ser substituível. Qwen3-VL é um candidato inicial para benchmark, não um acoplamento definitivo.

## 11. Dúvidas e pesquisa

Uma dúvida pode ser tratada em camadas:

1. contexto local da própria página;
2. conhecimento já acumulado no Notely;
3. pesquisa externa quando o usuário pedir.

Resultados de web search ou research devem ser persistidos com origem, URL, data de recuperação e ligação à dúvida que originou a pesquisa.

## 12. Grafo de conhecimento

O Notely deve projetar conhecimento derivado das anotações.

Exemplos de nós:

- Document;
- Annotation;
- UserNote;
- Question;
- Concept;
- Claim;
- ExternalSource.

Exemplos de relações:

- HAS_ANNOTATION;
- REFERS_TO;
- QUESTIONS;
- SUPPORTS;
- CONTRADICTS;
- RELATED_TO;
- ANSWERED_BY.

O grafo deve ser reconstruível a partir da fonte de verdade.

## 13. Proveniência

O sistema deve distinguir, no mínimo:

- USER_HIGHLIGHT;
- USER_NOTE;
- USER_QUESTION;
- USER_IMPORTANT;
- USER_DISAGREEMENT;
- SOURCE_CLAIM;
- AI_INFERENCE;
- MULTIMODAL_INFERENCE;
- EXTERNAL_RESEARCH.

Nenhuma inferência deve aparecer como se tivesse sido escrita pelo usuário ou declarada diretamente pelo documento.

## 14. Arquitetura de dados

A base será PostgreSQL com extensões complementares:

- PostgreSQL: fonte de verdade do domínio;
- TimescaleDB: eventos e histórico temporal;
- Apache AGE: projeção do grafo;
- pgvector: recuperação semântica e similaridade;
- Transactional Outbox: coordenação do processamento assíncrono.

AGE e pgvector são representações derivadas. Nenhuma informação importante pode existir apenas neles.

## 15. Processamento assíncrono

A criação de uma anotação deve ser rápida:

```text
ação do usuário
    ↓
transação PostgreSQL
    ├── grava dado de domínio
    └── grava outbox_event
    ↓
Reader recebe confirmação
    ↓
workers processam
    ├── evento temporal
    ├── embedding
    ├── conceitos
    ├── relações
    ├── grafo
    └── multimodal/pesquisa quando solicitado
```

Falha em IA, AGE, pgvector ou pesquisa não pode causar perda da anotação primária.

## 16. Requisitos não funcionais

- feedback local de highlight com sensação imediata;
- persistência fora de IA e processamento pesado;
- workers idempotentes;
- modelo e versão registrados nas inferências;
- conteúdo integral do documento não deve ser enviado externamente por padrão;
- anotações exportáveis em formato aberto;
- histórico e proveniência preservados;
- Reader funcional mesmo sem serviços de IA.

## 17. Critérios de aceitação do primeiro MVP

O primeiro MVP é utilizável quando:

- um PDF textual abre corretamente;
- highlight, comentário e dúvida podem ser criados;
- fechar e reabrir o documento restaura as marcações;
- annotation e outbox_event são persistidos na mesma transação;
- eventos temporais são registrados;
- embeddings podem ser gerados e consultados;
- AGE recebe projeção básica de annotations e concepts;
- falhas assíncronas são recuperáveis;
- uma região visual pode ser enviada ao provider multimodal;
- a resposta multimodal preserva proveniência;
- annotations nativas de PDF podem ser importadas;
- sidecar/exportação das anotações funciona.

## 18. Roadmap inicial

### Etapa 1 — Vertical slice sem IA

PDF.js → highlight → API → PostgreSQL + Outbox → reabrir → highlight reaparece.

### Etapa 2 — Eventos, embeddings e grafo

TimescaleDB + pgvector + AGE processados por workers idempotentes.

### Etapa 3 — Multimodal

Seleção de região → crop → provider multimodal → resposta contextualizada.

### Etapa 4 — Importação e portabilidade

Annotations nativas, sidecar e fallback para scan/highlight achatado.

### Etapa 5 — Pesquisa externa

Dúvida → busca explícita → fontes persistidas → relação com a dúvida.

### Etapa 6 — Integrações

Adapters opcionais, incluindo integração futura com Clarc.

## 19. Direção do produto

O Notely não deve competir com o usuário pela leitura. Ele deve transformar o ato de ler em memória recuperável.

A leitura continua humana. A organização, conexão, recuperação e assistência podem ser automatizadas.
