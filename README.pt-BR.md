# Plugin Pipefacil para Hermes

Plugin independente para o gateway do Hermes. Ele recebe webhooks `message.received` do Pipefacil,
consulta o histórico recente da conversa, permite que o profile responda ao lead pela API e oferece
uma ferramenta restrita para atualizar o negócio no CRM.

[Read this in English](README.md).

## O que ele faz

- Aceita webhooks `message.received` sem validar a assinatura; verifica o horário original de cada mensagem para rejeitar eventos antigos.
- Busca mensagens recentes de todos os participantes da conversa, não apenas as enviadas pelo Hermes.
- Envia a resposta final do agente ao lead pela API do Pipefacil.
- Registra `pipefacil_send_messages`, que permite enviar até duas mensagens adicionais (texto, imagem ou documento) antes da resposta final automática do Hermes.
- Baixa somente anexos das mensagens atuais do webhook e os encaminha ao processamento nativo de imagem/documento do Hermes.
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
- URL pública HTTPS ou túnel que encaminhe o callback ao gateway Hermes.

O histórico de conversas e o envio de respostas exigem o recurso `ADVANCED_API` do Pipefacil. Consulte
a [documentação da API do Pipefacil](https://developers.matchsales.com.br/api/).

## Instalação

Clone o repositório no diretório de plugins do profile e habilite o plugin:

```bash
PROFILE=sdr
PLUGIN_DIR="$HOME/.hermes/profiles/$PROFILE/plugins/pipefacil_sdr"
mkdir -p "$(dirname "$PLUGIN_DIR")"
git clone https://github.com/MatchSales/hermes-pipefacil-plugin.git "$PLUGIN_DIR"
hermes -p "$PROFILE" plugins enable pipefacil-platform
```

O diretório pode ser diferente se a instalação do Hermes usar um home personalizado. Guarde as
credenciais no arquivo `.env` do profile; não as adicione ao Git:

```dotenv
PIPEFACIL_API_KEY=pf_live_...
```

Reinicie o gateway após alterar arquivos do plugin, credenciais ou configuração do profile.

### Instalação em Docker e pelo painel

Na imagem oficial, os dados persistentes ficam em `/opt/data`. Confirme o nome exato do profile com
`hermes profile list` dentro do contêiner. O instalador da tela **Plugins** instala no profile que
hospeda o processo do painel; selecionar outro profile no topo da página não muda o destino da
instalação nessa versão do Hermes. Para um profile secundário, instale nele explicitamente:

**Se você só tem acesso ao Hermes Console do painel:** selecione o profile de atendimento no topo
da página, abra **System > Open console** e confirme o nome do profile exibido no cabeçalho.
Digite um comando por vez, sem `hermes`, `-p` ou comandos de shell:

```text
profile
profile list
plugins list --user --plain
```

Se o plugin estiver ausente, instale com
`plugins install https://github.com/MatchSales/hermes-pipefacil-plugin.git --enable`
(o repositório precisa estar acessível a esse Hermes). Se aparecer desabilitado, use
`plugins enable pipefacil-platform`. O Console pede confirmação para alterações. Depois cadastre
a chave da API em **Canais > Pipefacil > Configure** no mesmo profile. Para ligar o gateway, selecione
o profile `default` no topo do painel e use **System > Gateway > Start** se estiver parado, ou
**Restart** se estiver ativo. O Hermes Console não oferece o comando `gateway` nem acesso a
`/run/service`.

**Se você tem acesso ao terminal do host Docker**, o equivalente é:

```bash
docker exec -u hermes -it <container> hermes -p <profile> plugins install \
  https://github.com/MatchSales/hermes-pipefacil-plugin.git --enable
docker exec -u hermes <container> hermes -p <profile> plugins list --user --plain
```

Use `-u hermes` porque `docker exec` sem essa opção entra como root na imagem oficial e pode criar
arquivos que o gateway não consegue alterar. O comando usa Git e não exige `gh`. Quando o instalador
avisa que falta `PIPEFACIL_API_KEY`, o plugin pode já estar instalado e habilitado: cadastre a chave
no profile atendido, em **Canais > Pipefacil > Configure** ou no `.env` desse profile. O cartão
Pipefacil aparece quando o plugin é carregado no profile; a chave da API é necessária para conectá-lo.

Em Docker com gateway compartilhado, inicie ou reinicie o gateway do profile `default`, que atende
os profiles secundários:

```bash
docker exec -u hermes <container> hermes -p default gateway status
docker exec -u hermes <container> hermes -p default gateway start   # se estiver parado
docker exec -u hermes <container> hermes -p default gateway restart # se já estiver ativo
```

Se o painel mostrar `no such gateway '<profile>'`, confirme primeiro que o profile existe com
`hermes profile list` e que o gateway `default` está ativo. Não recrie um profile existente. O
serviço s6 em `/run/service/gateway-<profile>` é temporário; a inicialização do contêiner o
reconstrói a partir dos profiles persistentes.

## Configuração do profile

Habilite a plataforma e somente as ferramentas restritas do Pipefacil:

```yaml
platforms:
  pipefacil:
    enabled: true
    extra:
      host: 127.0.0.1 # usado quando este profile executa um gateway independente
      port: 8645      # no gateway compartilhado, vale a porta do listener default
      path: /events/message-received
      history_limit: 100 # de 1 a 200
      max_message_age_seconds: 300 # de 1 a 3600; horário original da mensagem
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

A allowlist `*` permite que qualquer identidade de lead recebida por webhook chegue ao agente. Esta
versão não autentica o webhook: qualquer pessoa que alcançar o callback pode enviar um evento que
dispare uma resposta do agente ou uma atualização no CRM. Restrinja o acesso na entrada pública e
use HTTPS.

### Proteção contra reenvios

Cada mensagem precisa de `timestamp` original com fuso horário, ou horário Unix numérico em segundos
ou milissegundos. Por padrão, mensagens com mais de cinco minutos, sem horário válido ou mais de
30 segundos no futuro são ignoradas com HTTP 200. O horário novo do envio do webhook não transforma
uma mensagem antiga em nova. Em lotes mistos, apenas as mensagens recentes entram. Isso vale também
para `/reset`.

O registro de mensagens admitidas fica em `<profile>/pipefacil-state/inbox.sqlite3` por sete dias,
separado por conversa e identidade da mensagem, sem guardar texto ou telefone. Preserve o volume do
profile nas atualizações. Reinícios, reconexões e `/reset` mantêm esse registro. Falha ao salvar o
registro retorna HTTP 503 antes de chamar o agente. Após a admissão, erros de processamento não
liberam o mesmo evento para repetir o envio, pois o resultado de um envio pode ser incerto. O cliente
pode mandar uma mensagem nova para continuar.

Para alterar a janela manualmente, edite o YAML do profile selecionado no dashboard, ajuste
`platforms.pipefacil.extra.max_message_age_seconds` e reinicie o gateway desse profile. O intervalo
permitido é de 1 a 3600 segundos; zero não desativa a proteção. A autenticação por assinatura continua
desativada.

O toolset `pipefacil` contém as ferramentas de resposta, atualização do negócio do evento atual e
`pipefacil_read_profile_file`. O envio de mensagens
usa o destinatário do evento atual; o modelo não escolhe telefone. A ferramenta aceita de uma a
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
acessível pelo host público. Consulte `hermes -p default gateway status` para obter o endereço e a
porta efetivos: em uma imagem Docker limpa testada, o callback compartilhado usou a porta `8642`,
embora `platforms.pipefacil.extra.port` fosse `8645`. O health check acrescenta `/health` ao callback,
por exemplo `http://127.0.0.1:8642/p/<profile>/events/message-received/health`. A porta `8645`
vale para um gateway Pipefacil independente.

O Hermes aceita eventos `message.received` sem conferir `X-Pipefacil-Signature-256` ou
`X-Pipefacil-Timestamp`. O endpoint confirma o recebimento do webhook; a geração da resposta e o
envio pela API acontecem em segundo plano. Um retorno `200` não confirma que o agente respondeu.

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

A versão 0.3.5 foi testada com Hermes 0.21.5 (`749220ef`) e dois profiles em gateway compartilhado.
O plugin bloqueia avisos internos de configuração, interrupção, fila, onboarding e erros, inclusive
em versões que chamam `send()` diretamente. Respostas automáticas exigem o turno atual do cliente;
reenvios de recuperação fora desse turno são suprimidos. As confirmações explícitas de `/reset`
continuam disponíveis aos números de teste autorizados. A observabilidade é preservada.

## Mídia recebida

Somente os anexos das mensagens atuais incluídas no webhook são baixados. O download exige HTTPS, não
segue redirecionamentos e tem limite de 25 MiB por arquivo. Imagens são encaminhadas à visão do Hermes;
documentos compatíveis são disponibilizados à ferramenta `pipefacil_read_profile_file`. Links expirados,
tipos incompatíveis, respostas vazias ou arquivos inválidos são descritos no contexto do agente; nesse
caso, ele não deve afirmar que leu ou analisou o conteúdo. Anexos antigos do histórico não são baixados.
PDFs digitalizados sem camada de texto podem não ser extraídos.

## Segurança e privacidade

- Mantenha `PIPEFACIL_API_KEY` no arquivo de segredos do profile, fora do Git.
- Proteja o callback público na entrada: esta versão não distingue um pedido do Pipefacil de um
  pedido forjado, que pode gerar mensagens ou atualizações no CRM.
- Use HTTPS entre o Pipefacil e a entrada pública.
- As mensagens do lead e o histórico recente são enviados ao modelo configurado no Hermes como
  contexto do turno. Considere o provedor do modelo e o acesso ao profile na sua política de dados.
- O plugin não baixa anexos do histórico antigo nem transcreve áudio.
- Consulte [SECURITY.md](SECURITY.md) para reportar vulnerabilidades com responsabilidade.

## Licença

Ainda não foi escolhida uma licença open source. Consulte [LICENSE-NOTICE.md](LICENSE-NOTICE.md);
deixar o repositório público não concede automaticamente direitos de reutilização.
