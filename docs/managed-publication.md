# Publicação gerenciada — Sprint 4

Capacidade administrativa privada: `install_managed_publication(expected_version)` exige admissão fechada, zero turnos ativos e versão correspondente. Não é uma ferramenta do modelo.

O marcador `.pipefacil-publication.json` é persistido pelo plugin administrativo depois da verificação dos arquivos. A revisão integra `SessionSource.thread_id`, preservando chat, destino, mensagens e histórico. A revisão é carregada na inicialização e na instalação sob a barreira; não há consulta ao disco por mensagem. Profiles não gerenciados mantêm a rota existente.

Validação local usa o SessionStore e o SessionDB nativos, sem chamadas a modelos ou WhatsApp. Ativação e despacho CRM v2 pertencem à Sprint 5.
