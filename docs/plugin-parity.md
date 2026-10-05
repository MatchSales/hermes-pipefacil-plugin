# Compatibilidade entre plugins HTTP e Kafka

## Par compatível desta atualização

HTTP **0.4.3**, Kafka **0.1.1**, revisão de orientação compartilhada **1**.
As versões de transporte evoluem separadamente; o contrato compartilhado identifica as regras comuns.

| Comportamento | HTTP | Kafka |
|---|---|---|
| Orientações internas por turno, sem interpolar dados externos | Sim | Sim |
| Nomes exatos, tool_describe e chamadas locais sequenciais | Sim | Sim |
| Consultar CRM antes de editar; preservar valores e confirmar a operação | Sim | Sim |
| Isolamento da conversa, destinatário e permissões | Sim | Sim |
| Mensagens de texto em partes | Até 2 adicionais + resposta final | De 1 a 8 explícitas; suprime final redundante |
| Leitura de anexos, visão e transcrição | Suportadas; dependem do modelo/STT/configuração | Não suportadas pelo schema 1 |
| Catálogo e envio de mídia com upload reutilizado | Sim | Não suportados pelo contrato atual |
| Idempotência | Fila/efeitos por webhook | Journal/outbox por comando/evento |

O health HTTP e o status Kafka expõem `pluginVersion`, `guidanceRevision` e `capabilities`.
`capabilities` descreve o contrato suportado pelo plugin, não comprova configuração válida,
permissões de CRM, disponibilidade de STT ou entrega física. O status Kafka mantém `crmEnabled`.

## Fonte e verificação das orientações comuns

`shared_guidance.py` é a fonte no repositório HTTP. O Kafka inclui uma cópia idêntica em
`pipefacil_kafka/shared_guidance.py`; isso evita dependência de pacote/pip no gateway em execução.
As regras específicas de cada transporte ficam em seus próprios `guidance.py`, com seus nomes
de ferramentas, limites e resultados. As descrições do registro e do schema são iguais.

O Kafka mantém em `compatibility/http-guidance.json` o commit HTTP imutável, a revisão e o hash
SHA-256 da cópia compatível. Sua CI baixa somente esse arquivo público, compara os bytes e não
executa código remoto. Não precisa acessar o repositório privado Kafka a partir do HTTP nem
adicionar credenciais. Uma falha de rede ou divergência bloqueia essa verificação.

Para comparar dois checkouts locais, execute no repositório Kafka:

```bash
python scripts/check_shared_guidance.py --http-plugin /caminho/do/hermes-pipefacil-plugin
```

Sem esse argumento, o comando verifica o commit público fixado na referência. A CI de cada
plugin testa o runtime publicado e o upstream atual; a CI Kafka também usa um broker isolado real.
A verificação não atualiza nem implanta plugins automaticamente e não estabelece paridade de
funcionalidades de mídia. Ela comprova igualdade das regras do par declarado.

## Processo para mudanças comuns

1. Preparar PRs nos dois repositórios para uma mudança das regras comuns.
2. Manter os dois arquivos compartilhados idênticos e atualizar sua revisão quando o contrato mudar.
3. Publicar o commit HTTP e atualizar commit/revisão/hash na referência Kafka.
4. Executar a comparação local, testes de descoberta, contexto por turno, isolamento e efeitos;
   conferir as duas matrizes de CI com Hermes sem fork.
5. Documentar o novo par, vincular as PRs e mesclar ambas antes de implantar o par declarado.
6. Atualizar cada profile explicitamente, reiniciar o gateway correspondente e conferir versão,
   revisão e capacidades. Não limpar journals ou históricos na atualização.

A referência é fixa para tornar o resultado reproduzível. Mudanças futuras em HTTP/main exigem
nova revisão do par; uma CI verde para uma referência antiga não declara compatibilidade com
qualquer versão posterior do HTTP.

## Próxima etapa de mídia Kafka

Ampliar mídia exige contrato versionado de entrada/saída, representação de anexos atuais,
validação de URLs/arquivos, integração nativa com visão/STT, aprovação de arquivos do profile,
recibos duráveis de upload, resultado incerto, tamanho/expiração e atualização do consumidor
responsável por entregar esses eventos no Pipe. Manter compatibilidade com comandos textuais
schema 1 exige negociação explícita de capacidade ou nova versão de schema; não basta liberar
ferramentas de envio nem copiar o prompt de mídia do HTTP.
