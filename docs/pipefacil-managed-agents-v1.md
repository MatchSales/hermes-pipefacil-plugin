# Preparação para agentes Hermes gerenciados pelo PipeFácil

Sprint 0, 09/10/2026. Plano canônico: `pipefacil-server/docs/hermes-integration-v1-plan.md`.
Contrato administrativo e runtime: `pipefacil-server/docs/hermes/contracts.md`.

Base de trabalho: `1442b85344001e95f2df25c2a1146222989c033e` (main), plugin `0.5.1`.
Nenhuma alteração de transporte ou comportamento foi feita nesta preparação.

## Mudanças futuras neste plugin

- Reconhecer agente selecionado e ligado no negócio, sem depender de ser responsável.
- Manter compatibilidade explícita para profiles/externos ainda no contrato antigo.
- Handoff do modelo novo apenas desliga IA, sem trocar responsável ou co-responsável.
- Mesmo profile responde em vários WhatsApps autorizados; sessão e recibos incluem o canal.
- Preservar HMAC, identidade/horário da mensagem, fila durável, recibos e efeitos incertos.
- Expor consulta autenticada por execução e admitir bloqueio/publicação por profile.
- Revalidar geração de ativação antes de efeitos; pausa/religamento não revalida turno antigo.
- Conteúdo comercial não libera ferramentas administrativas, terminal ou instalação de plugins.

O complemento administrativo será instalado uma vez por instalação e usará APIs nativas quando
elas derem as garantias necessárias. Este plugin continua instalado por profile. Não copiar seu
código para extensões comerciais ou para o template.

## Validação

A suíte exige o runtime Hermes; rodar apenas `pytest` em um Python sem o módulo `gateway` não
valida o plugin. `.github/workflows/ci.yml` fixa a imagem oficial por digest e também testa o
código atual do upstream, com `HERMES_SOURCE`, `PYTHONPATH`, `HERMES_HOME` e runtime de teste.

CI da base: https://github.com/MatchSales/hermes-pipefacil-plugin/actions/runs/37951331345
(commit `1442b85344001e95f2df25c2a1146222989c033e`, conclusão success).
O relatório do backend registra separadamente testes locais e evidências do ambiente remoto.

Baseline local: 376 testes Python aprovados com o snapshot Hermes disponível na máquina e
dependências em venv isolado; 12 testes de mídia aprovados com Node. Usar `HERMES_SOURCE`,
`PYTHONPATH`, `HERMES_HOME` e `HERMES_RUNTIME_DIR` isolados, conforme CI. O snapshot local não
atesta o digest da imagem em produção.
