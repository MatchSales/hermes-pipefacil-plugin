# Verificação da V0.4 — 5 de outubro de 2026

Escopo: plugin HTTP externo em `MatchSales/hermes-pipefacil-plugin`. Hermes e CRM não foram
alterados. Testes usaram profiles, contatos, leads e arquivos sintéticos isolados.

## Resultado por camada

| Camada | Evidência | Resultado |
|---|---|---|
| Contratos e runtime | 167 testes na imagem oficial Hermes v0.21.5 | Passou |
| Compatibilidade upstream | Mesmos 167 testes no snapshot `765342435609cc82cbeb3d664dcec24126da58ee` | Passou |
| Qualidade | Ruff e `git diff --check` | Passou |
| Assinatura do CRM | Vetor gerado pela classe Java compilada original; JSON UTF-8 e gzip verificados pelo plugin | Passou |
| HTTP | Sockets reais, listener próprio e compartilhado, assinatura, rotação, limites, duplicatas e fila cheia | Passou |
| Lifecycle | Base adapter nativo, ordem até conclusão, conversas paralelas, timeout e revogação de ferramentas | Passou |
| Upload/envio | API de teste com multipart real, renovação de link, dois turnos, deduplicação e resultado incerto | Passou |
| Leitura por modelo real | PNG, DOCX, PDF, TXT e lote misto imagem/documento/áudio | Conteúdo reconhecido |
| Transcrição real | HTTPS → download validado → STT nativo OpenAI `whisper-1`: WAV, OGG, MP3, M4A e FLAC | Passou |
| Áudio sem texto | Webhook só com áudio → gateway nativo → modelo identificou nome e plano do áudio | Passou |
| Biblioteca pelo modelo | Modelo listou/escolheu imagem e PDF por `fileId`; upload real e recusa explícita do link HTTP local | Passou na seleção/upload; envio não concluído |
| Storage real | Upload e link assinado via API pública do CRM; comparação SHA-256 dos bytes de PNG/PDF/DOCX/TXT | Passou |
| CRM real | Modelo alterou notas e campo autorizado; GET posterior confirmou; profile B preservado | Passou |
| Isolamento | Leitura de conhecimento em A → B → A, ferramentas restritas e negação de acesso a arquivos privados | Passou |
| WhatsApp | Dois canais locais consultados: `connected=false` | Entrega física não comprovada |

CI: [HTTP plugin checks](https://github.com/MatchSales/hermes-pipefacil-plugin/actions).
A CI executa a matriz oficial/atual novamente em cada commit; consulte o resultado do commit da PR.

## Limites da prova

Os modelos foram executados de verdade (`gpt-4.1-mini`), usando o gateway oficial sem modificar
seu código. Um proxy de loopback no ambiente de QA apenas encaminhou a API para o CRM local;
a origem HTTP de produção continua proibida fora de loopback. Arquivos de entrada sintéticos
foram servidos temporariamente por HTTPS público, sem dados de clientes ou credenciais.

O CRM local emitiu URLs de storage `http://`. O plugin exige HTTPS para envio e recusou essas
URLs. Upload e integridade dos objetos foram comprovados separadamente. O teste de envio com
resposta positiva da API usou um servidor de teste com sockets e multipart reais. Não equivale
a entrega no WhatsApp. As tentativas pelo CRM real encontraram canais desconectados/timeouts;
o journal registrou falha ou resultado incerto, sem repetir a escrita automaticamente.

Nos primeiros testes, o modelo tentou agrupar ferramentas locais e interpretar a transcrição
como texto digitado. A orientação de chamada foi corrigida, a leitura bruta de áudio/imagem
pela ferramenta de documentos foi recusada com instrução específica, e o cenário misto e o
áudio sem texto foram repetidos. M4A/FLAC expuseram aliases MIME de storage, agora normalizados
com validação da assinatura binária preservada. A configuração SQLite dos profiles de QA foi
ajustada para `database.journal_mode: delete` para evitar leitura de WAL entre host macOS e VM;
o journal do plugin já usa transações sem WAL.

Geração de novos arquivos, saída de áudio/vídeo e control plane não fazem parte desta versão.
Leitura de documentos depende dos extratores nativos disponíveis no Hermes. Imagem/áudio
exigem os respectivos recursos nativos configurados. Testes locais não validam a instalação
ou configuração específica de um cliente em produção.

## Antes de liberar entrega real

1. Conectar um canal WhatsApp de teste e usar um receptor de teste autorizado.
2. Configurar o storage do CRM para fornecer links HTTPS válidos e acessíveis pelo provedor.
3. Repetir imagem/documento saindo do profile até o receptor e conferir IDs/status no CRM.

O segredo do webhook deve ser o valor original do agente. Não é necessário `=` no final do
segredo. `sha256=` é o prefixo do cabeçalho, e `.env` usa `NOME=valor` para a atribuição.
