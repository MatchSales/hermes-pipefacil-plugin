# Atendimento gerenciado v2 — Sprint 5

Versão 0.8.0-dev, somente validação local nesta entrega. Profiles legados conservam o webhook assinado e o contrato anterior. Profiles gerenciados recebem comandos pela API privada do plugin administrativo; o modelo não controla identidade, execução ou destino.

O envelope contém workspace, agente, profile, geração, revisão e payload v2 do CRM. O journal SQLite registra execução e mensagem atomicamente, separa filas/sessões por canal e conversa e serializa turnos do mesmo negócio entre canais. Recebimentos duplicados consultam o recibo, preservando IDs e timestamps. A validade é o menor prazo entre execução e idade original da mensagem (10 minutos).

Leitura de contexto/histórico e efeitos usam `/api/v1/ai-agent`. A resposta usa o canal original definido pelo CRM. Antes de qualquer efeito há consulta do recibo; perder a resposta do POST permite apenas consultar esse recibo. Pendência ou incerteza interrompe o turno. Reinício nunca reexecuta trabalho que já começou.

Handoff gerenciado só pausa a IA do negócio, preserva ambos os responsáveis humanos e fecha os efeitos daquele turno. O comportamento legado permanece coberto por regressão. Conclusão é reportada por callback durável com cinco tentativas; reinício recupera callbacks pendentes, e o backend pode consultar o recibo após o prazo da execução. Tokens ausentes ficam desconhecidos.

Testes: `tests/test_managed_execution.py` usa o lifecycle nativo do adaptador e HTTP loopback, com geração sintética e sem modelo pago ou WhatsApp real. As suítes existentes continuam obrigatórias.
