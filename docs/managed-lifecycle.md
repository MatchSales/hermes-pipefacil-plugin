# Ciclo de vida gerenciado pelo PipeFácil

Sprint 3, plugin `0.6.0-dev`. Código e testes locais; sem ativação em instalações compartilhadas.

Cada profile continua usando este plugin para atendimento. A instalação usa separadamente
`hermes-pipefacil-admin-plugin` `0.2.0-dev`, sem ferramentas de gestão expostas ao modelo.
O template gerenciado começa com canais desligados, `managed_admission: true` e sem dados de
outro agente. Publicação de SOUL/skills e execução CRM v2 serão concluídas nas sprints seguintes.

O administrador resolve o adaptador no contexto explícito do profile pela API de extensões v1.
`install_managed_credentials` exige admissão fechada, nenhum turno ativo e a versão esperada.
A troca instala chave e segredo juntos e devolve somente o fingerprint SHA-256 da chave.
`retire_managed_profile` exige a mesma barreira, limpa as credenciais em memória e bloqueia
novos efeitos enquanto o multiplexer nativo retira o profile. O administrador arquiva os dados;
este plugin não apaga sessões, histórico ou registros de entrega.

Esses métodos são interfaces privadas de administração; não são ferramentas nem rotas CRM.
O modo gerenciado é opt-in. Agentes externos já existentes mantêm seu transporte e configuração.
A barreira pressupõe um único processo dono de cada profile, sem lease distribuído entre gateways.

Esta versão incorpora também as correções locais `7bf0889` (não enviar falha técnica como
resposta ao contato) e `4708f6c` (CI Python 3.14) da linha oficial, preservadas por cherry-pick.
O pacote fixa o commit completo do plugin e verifica seus arquivos antes do provisionamento.
