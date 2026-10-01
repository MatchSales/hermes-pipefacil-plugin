# Plugin Pipefacil para Hermes

Plugin independente para o gateway do Hermes. Ele recebe webhooks `message.received` do Pipefacil,
consulta o histórico recente da conversa, permite que o profile responda ao lead pela API e oferece
uma ferramenta restrita para atualizar o negócio no CRM.

[Read this in English](README.md).

## O que ele faz

- Valida a assinatura HMAC-SHA256 do webhook e rejeita timestamps fora da janela de cinco minutos.
- Busca mensagens recentes de todos os participantes da conversa, não apenas as enviadas pelo Hermes.
- Envia a resposta final do agente ao lead pela API do Pipefacil.
- Registra `pipefacil_send_messages`, que permite enviar até duas mensagens adicionais (texto, imagem ou documento) antes da resposta final automática do Hermes.
- Baixa somente anexos das mensagens atuais do webhook assinado e os encaminha ao processamento nativo de imagem/documento do Hermes.
- Se não conseguir carregar o histórico do Pipefacil, usa o histórico local do Hermes que estiver
  disponível para aquela conversa.
- Trata uma mensagem isolada `/reset` como comando do Hermes, remove o transcript local da sessão
  encerrada e deixa de reenviar ao modelo mensagens antigas do histórico Pipefacil daquela conversa.
- Registra a ferramenta `pipefacil_update_deal` para atualizar um negócio identificado pelo `seq`.
- Lê as credenciais do profile atendido e não envia `workspaceId`.
- Mídias recebidas de mensagens antigas do histórico aparecem como conteúdo não textual e não são baixadas.
- Links de mídia enviados precisam ser HTTPS e estar explicitamente cadastrados no `SOUL.md` do profile ativo.

## Requisitos

- Hermes com suporte a plugins de gateway e registro de plataformas.
- Chave da API do Pipefacil com `API_ACCESS`, `ADVANCED_API`, acesso de leitura/envio de conversas e
  permissão para editar negócios.
- Segredo de assinatura configurado para o webhook do Pipefacil.
- URL pública HTTPS ou túnel que encaminhe o callback ao gateway Hermes.

O histórico de conversas e o envio de respostas exigem o recurso `ADVANCED_API` do Pipefacil. Consulte
a [documentação da API do Pipefacil](https://developers.matchsales.com.br/api/).

## Instalação

Clone o repositório no diretório de plugins do profile e habilite o plugin:

```bash
PROFILE=sdr
PLUGIN_DIR="$HOME/.hermes/profiles/$PROFILE/plugins/pipefacil_sdr"
mkdir -p "$(dirname "$PLUGIN_DIR")"
git clone https://github.com/cardosolucass96/hermes-pipefacil-plugin.git "$PLUGIN_DIR"
hermes -p "$PROFILE" plugins enable pipefacil-platform
```

O diretório pode ser diferente se a instalação do Hermes usar um home personalizado. Guarde as
credenciais no arquivo `.env` do profile; não as adicione ao Git:

```dotenv
PIPEFACIL_API_KEY=pf_live_...
PIPEFACIL_WEBHOOK_SECRET=...
```

Reinicie o gateway após alterar arquivos do plugin, credenciais ou configuração do profile.

## Configuração do profile

Habilite a plataforma e somente as ferramentas restritas do Pipefacil:

```yaml
platforms:
  pipefacil:
    enabled: true
    extra:
      host: 127.0.0.1
      port: 8645
      path: /events/message-received
      history_limit: 100 # de 1 a 200
      allowed_users:
        - "*"
      reset_allowed_users: [] # números de teste autorizados, com DDI e DDD
      # Opcional para um servidor local ou de homologação:
      # api_base_url: https://homolog.pipefacil.matchsales.com.br

platform_toolsets:
  pipefacil: [pipefacil]

agent:
  max_turns: 50
```

A allowlist `*` permite que identidades de leads recebidas por webhooks assinados cheguem ao agente.
O plugin valida a assinatura antes de encaminhar o evento. Mantenha o segredo privado e não exponha
o listener diretamente por HTTP sem TLS.

O toolset `pipefacil` contém as ferramentas de resposta, atualização do negócio do evento autenticado e
`pipefacil_read_profile_file`. O envio de mensagens
usa o destinatário do evento autenticado; o modelo não escolhe telefone. A ferramenta aceita de uma a
duas mensagens e o Hermes envia sua resposta final automaticamente depois. Um retorno de sucesso confirma
que a API aceitou a requisição, mas não confirma a entrega pelo WhatsApp. A leitura é limitada aos
anexos do turno atual e aos arquivos em `knowledge/` dentro do próprio profile. O toolset Hermes `file`
não deve ser habilitado no atendimento público, pois também permite escrever e alterar arquivos.

### Biblioteca de mídia do profile

Cadastre cada link HTTPS que o agente pode compartilhar no `SOUL.md` do próprio profile. Uma entrada deve
conter rótulo, tipo (`image` ou `document`) e URL exata:

```text
- label: Apresentação institucional | type: document | url: https://bucket.example.com/apresentacao.pdf?assinatura=...
- label: Foto da equipe | type: image | url: https://bucket.example.com/equipe.jpg?assinatura=...
```

O plugin só aceita a combinação exata de tipo e URL encontrada no `SOUL.md` do profile ativo. Links
assinados precisam continuar válidos e acessíveis ao WhatsApp quando forem enviados; links expirados
retornam falha da API. Não adicione credenciais a URLs ou ao Git além dos parâmetros de assinatura
necessários ao link.

## URL do webhook

Para um gateway de profile independente, configure no Pipefacil:

```text
https://<seu-host-publico>/events/message-received
```

Para um profile secundário atendido pelo multiplexador Hermes do profile `default`:

```text
https://<seu-host-publico>/p/<profile>/events/message-received
```

A rota de profile compartilhada exige que o listener HTTP do gateway `default` esteja habilitado e
acessível pelo host público. O health check local acrescenta `/health` ao callback, por exemplo
`http://127.0.0.1:8645/events/message-received/health`.

O Pipefacil envia a assinatura no header `X-Pipefacil-Signature-256` e o timestamp em
`X-Pipefacil-Timestamp`. O Hermes valida HMAC-SHA256 sobre `<timestamp>.<JSON body>` e rejeita
timestamps fora da janela de cinco minutos. O endpoint confirma o recebimento do webhook; a geração
da resposta e o envio pela API acontecem em segundo plano.

## Contexto da conversa

Antes de cada turno, o plugin busca até `history_limit` mensagens recentes pelo telefone do contato,
usando o canal Pipefacil quando disponível. O histórico inclui mensagens recebidas e enviadas por
todos os participantes. A mensagem mais recente do webhook é apresentada separadamente caso ainda
não esteja no histórico da API.

Se a consulta falhar, o agente recebe a instrução de usar o histórico local do Hermes que estiver
disponível e fazer uma pergunta curta se faltar contexto. Esse histórico local pode não incluir
mensagens trocadas fora do Hermes, e a disponibilidade depende do suporte de restauração de sessões
da versão instalada. A chave da API ainda é necessária para responder e atualizar o CRM.

Um número listado em `reset_allowed_users` pode enviar `/reset` sozinho para começar do zero. O Hermes abre uma sessão nova, o plugin
apaga o transcript local da sessão anterior e ignora o histórico Pipefacil anterior à mensagem de
reset nos próximos turnos. As mensagens originais continuam no CRM; o comando limpa o contexto do
agente, não apaga a conversa do Pipefacil.

## Compatibilidade

Quando o Hermes oferece o recurso de plataforma `notify_missing_home_channel`, o plugin desativa o
aviso pessoal `/sethome` para os leads do Pipefacil. Em versões antigas, o plugin continua carregando,
mas o Hermes pode exibir seu aviso normal de canal inicial em uma conversa nova.

## Mídia recebida

Somente os anexos das mensagens atuais incluídas no webhook são baixados. O download exige HTTPS, não
segue redirecionamentos e tem limite de 25 MiB por arquivo. Imagens são encaminhadas à visão do Hermes;
documentos compatíveis são disponibilizados à ferramenta `pipefacil_read_profile_file`. Links expirados,
tipos incompatíveis, respostas vazias ou arquivos inválidos são descritos no contexto do agente; nesse
caso, ele não deve afirmar que leu ou analisou o conteúdo. Anexos antigos do histórico não são baixados.
PDFs digitalizados sem camada de texto podem não ser extraídos.

## Segurança e privacidade

- Mantenha `PIPEFACIL_API_KEY` e `PIPEFACIL_WEBHOOK_SECRET` no arquivo de segredos do profile, fora
  do Git.
- Use HTTPS entre o Pipefacil e a entrada pública.
- As mensagens do lead e o histórico recente são enviados ao modelo configurado no Hermes como
  contexto do turno. Considere o provedor do modelo e o acesso ao profile na sua política de dados.
- O plugin não baixa anexos do histórico antigo nem transcreve áudio.
- Consulte [SECURITY.md](SECURITY.md) para reportar vulnerabilidades com responsabilidade.

## Licença

Ainda não foi escolhida uma licença open source. Consulte [LICENSE-NOTICE.md](LICENSE-NOTICE.md);
deixar o repositório público não concede automaticamente direitos de reutilização.
