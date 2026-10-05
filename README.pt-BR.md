# Plugin Pipefacil para Hermes

Plugin independente para o gateway do Hermes. Ele recebe webhooks `message.received` do Pipefacil,
consulta o histórico recente da conversa, permite que o profile responda ao lead pela API e oferece
uma ferramenta restrita para atualizar o negócio no CRM.

[Read this in English](README.md).

## O que ele faz

- Autentica webhooks `message.received` com HMAC-SHA256, inclusive quando o backend compacta o JSON com gzip; rejeita eventos antigos.
- Busca mensagens recentes de todos os participantes da conversa, não apenas as enviadas pelo Hermes.
- Envia a resposta final do agente ao lead pela API do Pipefacil.
- Registra `pipefacil_send_messages`, que permite enviar até duas mensagens adicionais (texto, imagem ou documento) antes da resposta final automática do Hermes.
- Baixa somente anexos atuais e usa visão, leitura de documentos e transcrição de áudio nativas do Hermes.
- Oferece catálogo de imagens/documentos em `media/` no próprio profile, com upload na API existente do Pipefacil e links temporários.
- Persiste a fila e as tentativas de envio antes de confirmar a admissão; ordena toda a execução por conversa.
- Se não conseguir carregar o histórico do Pipefacil, usa o histórico local do Hermes que estiver
  disponível para aquela conversa.
- Trata uma mensagem isolada `/reset` como comando do Hermes, remove o transcript local da sessão
  encerrada e deixa de reenviar ao modelo mensagens antigas do histórico Pipefacil daquela conversa.
- Registra a ferramenta `pipefacil_update_deal` para atualizar um negócio identificado pelo `seq`.
- Lê as credenciais do profile atendido e não envia `workspaceId`.
- Mídias recebidas de mensagens antigas do histórico aparecem como conteúdo não textual e não são baixadas.
- Arquivos locais enviados vêm de `media/` do profile. Links externos precisam ser HTTPS e estar cadastrados no `SOUL.md` desse profile.

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
PIPEFACIL_WEBHOOK_SECRET=segredo_original_do_agente_no_pipefacil
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

A allowlist `*` permite que leads de eventos autenticados cheguem ao agente. O backend já seleciona
os eventos conforme as regras do agente; o plugin verifica a assinatura para comprovar essa origem.
Use HTTPS na entrada pública. As ferramentas disponíveis no atendimento são restritas pelo adapter
e por um hook em tempo de execução, mesmo que outros toolsets estejam habilitados no YAML.

### Proteção contra reenvios

Cada mensagem precisa de `timestamp` original com fuso horário, ou horário Unix numérico em segundos
ou milissegundos. Por padrão, mensagens com mais de cinco minutos, sem horário válido ou mais de
30 segundos no futuro são ignoradas com HTTP 200. O horário novo do envio do webhook não transforma
uma mensagem antiga em nova. Em lotes mistos, apenas as mensagens recentes entram. Isso vale também
para `/reset`.

O registro, a fila privada e o diário de efeitos ficam em `<profile>/pipefacil-state/inbox.sqlite3`,
com diretório 0700 e arquivo 0600. A fila guarda o webhook para recuperar trabalhos ainda não iniciados.
Os dados de trabalhos encerrados são removidos após sete dias; efeitos incertos mantêm referências
de auditoria. Preserve o volume nas atualizações. Reinícios e `/reset` mantêm a deduplicação.
Falha ao salvar retorna HTTP 503; fila cheia retorna 429, sem admitir as mensagens. Trabalhos iniciados
antes de uma queda ficam interrompidos e não são executados novamente automaticamente. O cliente pode
mandar uma mensagem nova para continuar. Veja [operação e reconciliação](docs/http-runtime.md).

Para alterar a janela manualmente, edite o YAML do profile selecionado no dashboard, ajuste
`platforms.pipefacil.extra.max_message_age_seconds` e reinicie o gateway desse profile. O intervalo
permitido é de 1 a 3600 segundos; zero não desativa a proteção. A janela da assinatura é de cinco minutos.

### Segredo e erros 401

`PIPEFACIL_WEBHOOK_SECRET` recebe o **segredo original** do agente, literalmente, sem `sha256=`.
Não converta hexadecimal para bytes e não use a assinatura de uma entrega como segredo.
O `=` de `.env` separa o nome da variável do valor; não faz parte do segredo, salvo se o próprio
segredo tiver esse caractere. O backend envia `sha256=<HMAC>` no cabeçalho da requisição.
Ele calcula HMAC sobre `timestamp + "." + JSON original` **antes** de compactar. O plugin verifica
os mesmos bytes descompactados, sem reserializar JSON. `PIPEFACIL_WEBHOOK_SECRET_NEXT` permite rotação.
Um 401 no webhook indica assinatura/horário incorretos. Um 401 na API de saída indica chave da API
incorreta; são credenciais diferentes. Veja [contrato HTTP e diagnóstico](docs/http-runtime.md).

### Arquivos no próprio profile

Coloque referências para consulta em `knowledge/` e imagens/documentos para envio em `media/`:

```text
<profile>/
  SOUL.md
  knowledge/guia-comercial.pdf
  media/catalogo.pdf
  media/fotos/equipe.jpg
```

O modelo chama `pipefacil_list_media`, escolhe um `fileId` e usa `pipefacil_send_messages`.
O plugin valida o arquivo, faz upload pela API pública existente e renova o link temporário antes
de enviar. Ele reutiliza o upload enquanto o conteúdo for o mesmo. Limite: 16 MiB por arquivo,
200 itens no catálogo, imagens JPEG/PNG/GIF/WebP e documentos PDF/Office/TXT/CSV; links simbólicos,
hardlinks, arquivos ocultos, SVG e ZIP são recusados. URLs já cadastradas no SOUL continuam aceitas.

### Orientações automáticas e descoberta de ferramentas (0.4.2)

O plugin acrescenta um bloco estático de instruções pelo `MessageEvent.channel_prompt` nativo
do Hermes em cada turno de atendimento Pipefacil. Esse contexto complementa o profile sem
editar seu `SOUL.md` e vale também para a próxima mensagem de uma conversa existente após
atualizar o plugin e reiniciar o gateway. Não contém texto do lead, arquivos, links ou segredos;
esses dados continuam separados das instruções do canal.

Para pedidos como “me manda a apresentação”, o agente recebe orientação para consultar
`pipefacil_list_media`, escolher o arquivo adequado e enviar pelo `fileId` com
`pipefacil_send_messages`. Uma pasta `knowledge/` vazia não comprova ausência de arquivos em
`media/`. Catálogo indisponível não deve ser tratado como vazio, e arquivos ambíguos exigem
uma pergunta curta. O operador ainda precisa cadastrar os materiais e regras comerciais no profile.

Se o Hermes diferir ferramentas, o contexto orienta consultar `tool_describe` pelo nome exato
e executar uma ferramenta por `tool_call`. Como alternativa, `tool_search` deve receber somente
o nome exato, com underscores. A busca do Hermes é lexical: termos adicionais ausentes da
descrição podem eliminar todos os resultados. As descrições dos cinco schemas agora incluem
termos de mídia e CRM em português e inglês e são iguais às descrições do registro.

Essa atualização orienta o modelo; não garante que todo pedido natural será interpretado
corretamente. Para validar após implantação, cadastre uma apresentação sintética identificável,
peça seu envio sem mencionar ferramentas ou IDs e confira o anexo realmente recebido. Repita
o pedido e compare a chave do objeto no CRM para verificar o reaproveitamento. Em outra conversa
de teste, confirme o comportamento com catálogo vazio. O aceite da API sozinho não comprova entrega.

Atualize explicitamente o profile atendido com `hermes -p <profile> plugins update pipefacil-platform`
(ou `plugins update pipefacil-platform` no Console desse profile) e reinicie seu gateway; em
gateway compartilhado, reinicie o `default`. Não é necessário limpar histórico ou mudar o SOUL.

Para atualizar negócios, configure `PIPEFACIL_MEMBER_USER_ID` com o **userId do responsável**
do agente, `PIPEFACIL_CUSTOM_FIELDS` com slugs permitidos e `PIPEFACIL_STAGE_IDS` com IDs permitidos,
separados por vírgula. `pipefacil_current_deal` mostra o negócio atual e suas permissões. O plugin
consulta contato/responsável antes da alteração e confirma os valores em uma nova consulta depois.
Sem o userId configurado, conversas continuam disponíveis e alterações de negócio ficam desabilitadas.

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

A versão 0.4 é verificada com a imagem oficial Hermes 0.21.5 e o código upstream atual, incluindo dois profiles em gateway compartilhado.
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
- O callback público exige a assinatura HMAC do agente. Preserve o segredo original em cada profile.
- Use HTTPS entre o Pipefacil e a entrada pública.
- As mensagens do lead e o histórico recente são enviados ao modelo configurado no Hermes como
  contexto do turno. Considere o provedor do modelo e o acesso ao profile na sua política de dados.
- O plugin não baixa anexos do histórico antigo. Áudios atuais usam o provedor de transcrição nativo configurado no Hermes.
- Consulte [SECURITY.md](SECURITY.md) para reportar vulnerabilidades com responsabilidade.

## Licença

Ainda não foi escolhida uma licença open source. Consulte [LICENSE-NOTICE.md](LICENSE-NOTICE.md);
deixar o repositório público não concede automaticamente direitos de reutilização.

## Compatibilidade HTTP/Kafka (0.4.3)

As orientações comuns têm revisão e fonte compartilhadas com o Kafka 0.1.1. Consulte
[o contrato de compatibilidade e atualização coordenada](docs/plugin-parity.md).
O health informa versão, revisão e capacidades. Os nomes e limites específicos de mídia permanecem próprios deste canal.
