# Externos com o campo Agente de IA

Um profile externo conserva seu webhook HMAC, chave individual CRM, Prompt, skills,
extensões e sessões. Não vira profile administrado e não precisa do plugin administrativo.

O webhook assinado de workspace convertido acrescenta `execution` ao payload
`message.received`: `protocolVersion: 2`, `executionId`, `agentId`, `dealId`,
`generation`, `agentVersion`, `channelId`, `conversationId` e `expiresAt`.
O plugin valida formato, vínculo com os dados do evento, canal permitido e horário original.
A existência de `execution` exige v2 válido; não há fallback silencioso para o protocolo antigo.
Contextos privados não podem ser fornecidos pelo webhook, mesmo com assinatura válida.

O journal SQLite privado registra ID/hash/job atomicamente junto aos recibos de mensagens.
Mesmo ID e conteúdo retorna duplicidade; mesmo ID com outro contexto ou conteúdo retorna 409.
Reenvio com URL temporária renovada preserva a identidade. O prazo da fila é o menor entre
`expiresAt`, dez minutos e `max_message_age_seconds` contado do horário original da mensagem.
Reinício recupera somente trabalho que ainda não começou; trabalho iniciado fica interrompido.
O mesmo negócio é serializado entre canais, dentro do profile; negócios distintos podem avançar.

Antes de executar o modelo, o plugin consulta
`GET /api/v1/ai-agent/executions/{executionId}` usando a chave CRM do próprio profile.
O backend resolve tenant/identidade pela chave e valida atribuição, geração, versão,
prazo, canal e permissões. O plugin confronta a execução retornada e o contato do negócio.
O responsável humano não precisa ser o bot. Histórico vem de `.../{executionId}/messages`
e inclui mensagens sincronizadas do CRM ou aparelho, nas últimas 100 do canal original.

Envio, edição e handoff usam as mesmas APIs de execução e os recibos de idempotência dos
profiles gerenciados. A resposta vai ao destino fixado pelo backend; pausa, reatribuição,
revogação ou expiração impede novos efeitos. Resposta HTTP perdida é conciliada por GET de
recibo, sem repetir POST. Handoff somente pausa a IA e preserva os responsáveis humanos.
Externos não enviam o callback de conclusão exclusivo do transporte Hermes gerenciado.

## Corte de um workspace

1. Atualizar o plugin em cada profile externo preservando configuração e estado.
2. Conferir `webhookExecutionProtocols: [1, 2]` no health efetivamente carregado.
3. Validar o consumidor real, inclusive extensões, outboxes, mídias e integrações particulares.
4. Registrar `assignmentProtocolVersion: 2` na configuração externa do CRM.
5. Seguir inventário, drenagem e conversão por workspace do runbook do backend.

Consumidores de terceiros precisam implementar v2 antes do corte. O plugin não altera
esses sistemas. Manter o workspace em LEGACY preserva seus contratos anteriores;
retirar a trava de compatibilidade liberaria consumidores incapazes de responder com
segurança no novo vínculo. Marcar v2 não é prova de teste nem atualização automática.

O suporte foi acrescentado em `0.8.1-dev`, depois da PR #12: ainda precisa ser publicado e
instalado. Os pins de pacotes gerenciados continuam na revisão já aprovada; não apontam
automaticamente para este checkout modificado.
