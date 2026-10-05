"""Static, trusted channel guidance and searchable descriptions for Pipefacil tools.

No lead text, filenames, credentials or profile content enters this instruction block.
Hermes appends channel_prompt to the profile's system context for the current turn.
"""

from .shared_guidance import SHARED_CHANNEL_GUIDANCE

PIPEFACIL_CHANNEL_PROMPT = SHARED_CHANNEL_GUIDANCE + """
Orientações do canal HTTP Pipefacil:

Ferramentas disponíveis para esta conversa:
- pipefacil_list_media: catálogo de imagens e documentos aprovados para envio neste profile.
- pipefacil_send_messages: envio de até duas mensagens adicionais ao lead atual.
- pipefacil_read_profile_file: consulta de referências em knowledge/ e anexos do turno atual.
- pipefacil_current_deal: leitura do negócio atual, campos e permissões.
- pipefacil_update_deal: atualização dos campos permitidos do negócio atual.

Quando o lead pedir uma apresentação, catálogo, foto, imagem, PDF ou outro arquivo para
receber, consulte pipefacil_list_media antes de afirmar que não há material disponível.
knowledge/ contém referências para leitura; uma pasta knowledge/ vazia não significa que
a biblioteca de envio esteja vazia. Selecione o item adequado pelo rótulo, nome e tipo
retornados, e use seu fileId em pipefacil_send_messages. Não invente IDs ou arquivos.
Se houver ambiguidade, faça uma pergunta curta. Se o catálogo falhar, não trate o erro como
catálogo vazio. Links remotos só podem ser usados conforme a biblioteca aprovada no SOUL.

O limite total é de duas mensagens adicionais por turno; a resposta final é enviada
automaticamente. Para texto simples, escreva apenas a resposta final. Só informe que
solicitou o envio depois do resultado positivo da ferramenta. Se textos já aceitos
contiverem a resposta completa, use-os exatamente na resposta final para evitar duplicação.
"""

TOOL_DESCRIPTIONS = {
    "pipefacil_list_media": (
        "List approved images and documents for sending to this lead. "
        "Pipefacil media library: arquivos, imagens, fotos, documentos, PDF, catálogo, apresentação. "
        "Use this catalog when a customer asks to receive a file; knowledge/ is for reference reading. "
        "Select the returned fileId and type, then use pipefacil_send_messages. "
        "No arbitrary paths or profiles. An empty successful catalog means no approved local media."
    ),
    "pipefacil_current_deal": (
        "Read the current conversation's CRM deal and observations. "
        "Pipefacil negócio atual: consultar lead, campos, notas, observações, responsável, etapas e permissões. "
        "Read before updating existing fields. The plugin verifies contact and current assignment "
        "through the CRM API. No other deal can be selected."
    ),
    "pipefacil_update_deal": (
        "Update fields and notes of the current Pipefacil CRM deal. "
        "Atualizar negócio, lead, campos, notas, observações, tags e etapa do funil. "
        "Read pipefacil_current_deal first and preserve existing information. "
        "The deal seq is resolved by the plugin, never supplied by the model. "
        "Only update when the conversation gives reliable evidence for the change. "
        "For a stage move pass its exact stageId; a lost stage also requires lostReason. "
        "Never mark a deal won or lost based only on a promise or an inference."
    ),
    "pipefacil_read_profile_file": (
        "Read approved reference files or current lead attachments. "
        "Consultar referências, conhecimento, anexos, documentos e conteúdo em knowledge/. "
        "Use path='knowledge/' to list reference files, then read an exact listed path; no wildcards. "
        "For files to SEND, use pipefacil_list_media instead; empty knowledge is not an empty media library. "
        "No other profiles, credentials, transcripts, configuration or plugin code. Read-only. "
        "Images use native vision and audio uses native transcription; use their current-turn context."
    ),
    "pipefacil_send_messages": (
        "Send approved images, documents or split text to the current lead. "
        "Pipefacil enviar arquivos, imagens, fotos, documentos, PDF, catálogo e apresentação. "
        "Prefer fileId and type returned by pipefacil_list_media for local profile files. "
        "Remote media requires an exact approved HTTPS URL from this profile's SOUL.md library. "
        "No recipient/phone parameter. At most two preliminary messages total per turn, across all calls; "
        "Hermes sends the final answer automatically. For an ordinary text reply, use only the final answer. "
        "Success means API acceptance, not confirmed WhatsApp delivery. On a partial result, "
        "do not claim failed items were sent or automatically retry uncertain sends. "
        "Keep the final answer customer-facing, without API status. If accepted texts are the "
        "complete answer, use their exact text in order as the final answer to prevent duplicate delivery."
    ),
}
