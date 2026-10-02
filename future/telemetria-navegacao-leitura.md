# Future — Telemetria de navegação e tempo ativo de leitura

Origem: escopo deliberadamente adiado da Issue #1  
Issue de origem: https://github.com/fcoelopes/notely/issues/1

## Contexto

A primeira implementação do TimescaleDB deve registrar somente fatos temporais essenciais:

- `reading.started`;
- `reading.ended`;
- `annotation.created`;
- `annotation.updated`.

Eventos de navegação detalhada não fazem parte da primeira entrega.

## Objetivo futuro

Avaliar se há valor real em registrar comportamento de navegação durante a leitura para responder perguntas como:

- quais páginas foram efetivamente visitadas;
- em que páginas o usuário permaneceu mais tempo;
- quando uma sessão foi pausada e retomada;
- qual foi o tempo ativo aproximado de leitura;
- em que sequência diferentes partes do documento foram consultadas.

## Eventos candidatos

```text
page.viewed
reading.paused
reading.resumed
reader.focused
reader.blurred
```

Esses nomes ainda não constituem contrato de domínio.

## Questões que precisam ser respondidas antes da implementação

1. Qual decisão ou experiência do usuário depende desses dados?
2. Qual frequência de eventos é suficiente sem gerar telemetria excessiva?
3. Como distinguir documento aberto de leitura ativa?
4. Como lidar com troca de aba, bloqueio de tela e inatividade?
5. Devemos armazenar eventos brutos ou agregações?
6. Qual retenção é necessária?
7. Há benefício real em registrar `page.viewed` para PDFs longos?

## Restrições

- não transformar o Notely em ferramenta de vigilância ou produtividade por padrão;
- não registrar eventos sem uso claro;
- não bloquear o Reader para emitir telemetria;
- não usar esses eventos como fonte de verdade para annotations ou sessões;
- permitir desligar telemetria detalhada caso ela seja implementada.

## Critério para sair de future

Só promover este plano para issue quando existir um caso de uso concreto que justifique a telemetria adicional.
