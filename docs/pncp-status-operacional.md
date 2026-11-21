
## DOC-PNCP-06/08/09/12 — situação oficial e operacional (pncp-status-v1)

`official_status` preserva o texto oficial recebido; `derived_status` é a situação operacional calculada. `reason_codes`, `data_confidence`, `evaluated_at` e `status_rule_version` tornam a decisão auditável. A avaliação usa apenas o dado local, o instante informado e esta versão da regra.

| Precedência | Evidência | Estado operacional | Confiança |
|---|---|---|---|
| 1 | situação oficial contém anulação | ANULADA | alta |
| 1 | situação oficial contém revogação | REVOGADA | alta |
| 1 | situação oficial contém suspensão | SUSPENSA | alta |
| 2 | homologação oficial parcial, ou itens FINAL e PARCIAL | HOMOLOGADA_PARCIAL | alta |
| 2 | homologação oficial total, ou todos os itens FINAL | HOMOLOGADA_TOTAL | alta |
| 2 | adjudicação/resultado publicado sem estado de homologação | RESULTADO_PUBLICADO | alta |
| 3 | encerramento oficial explícito | ENCERRADA | alta |
| 4 | estado impeditivo junto de resultado ou janela aberta | estado impeditivo (precedência) | alta |
| 5 | datas ausentes, sem fuso ou janela invertida | INDETERMINADO | baixa |
| 6 | agora anterior ao início efetivo | ABERTURA_FUTURA | alta |
| 7 | dentro da janela e faltam até 48h | ENCERRANDO_EM_BREVE | alta |
| 7 | dentro da janela, faltam mais de 48h | ABERTA | alta |
| 8 | agora igual ou posterior ao prazo | ENCERRADA | alta |

“Divulgada no PNCP” não constitui evidência de abertura. Datas sem offset não são interpretadas como horário local. Situação oficial contraditória prevalece só nos casos impeditivos explícitos; payloads desconhecidos ou prazo sem fuso ficam indeterminados. Modalidade e forma de participação são preservadas como evidência, mas a regra não cria restrições por códigos ainda não homologados. Uma versão oficial nova atualiza official_status e recalcula o estado; eventos de alteração guardam before/after e chave idempotente no mesmo commit da visão.

O estado local de item `PARCIAL` significa que há resultados disponíveis sem homologação confirmada e, sozinho, resulta em `RESULTADO_PUBLICADO`. Só a evidência de item `FINAL` permite inferir homologação total (todos `FINAL`) ou parcial (mistura de `FINAL` com itens ainda não `FINAL`).

O processo `python scripts/pncp_status_scheduler.py` reavalia a visão local a cada 60 segundos sem chamadas ao PNCP, permitindo transições por passagem do tempo. Execute-o como processo operacional dedicado. A normalização de uma nova versão atualiza o estado na mesma transação.

Em produção, o serviço Compose `pncp-status-scheduler` executa essa reavaliação e corrige estados persistidos que tenham sido calculados sem a data de abertura. Atualizações de resultados de itens recuperam as datas da oportunidade antes de recalcular o estado, mesmo quando o chamador não as informa explicitamente.
