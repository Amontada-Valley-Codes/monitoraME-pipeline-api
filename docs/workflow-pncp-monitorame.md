# MonitoraME ? Workflow de Ingest?o e Processamento do PNCP

Refer?ncia recebida integralmente do solicitante em 2026-10-21. Este arquivo
preserva o recorte das se??es necess?rias ? tarefa P?s-Ingest?o 01/05; n?o
substitui o contrato OpenAPI vigente. Status do documento original: proposta
t?cnica baseada em Resili?ncia da ingest?o do PNCP (outubro/2026).

Escopo: m?dulo de oportunidades, contrata??es, atualiza??es e enriquecimento
progressivo. Orquestra??o: PostgreSQL como fila persistente; sem RabbitMQ.
Coleta e processamento s?o independentes; normaliza??o deve continuar sobre
RAW persistido quando a fonte estiver indispon?vel. Um job RAW conclu?do n?o
implica normaliza??o ou enriquecimento conclu?dos.

## 6. Persist?ncia RAW, idempot?ncia e atualiza??es

O payload ? armazenado como JSONB inicialmente; object storage pode ser adotado
se volume/reten??o justificarem. Chaves ?nicas propostas:
`(source, entity_type, external_id, payload_hash)` no hist?rico RAW e
`(source, entity_type, external_id)` na vis?o normalizada atual.
N?o assumir que todas as entidades usam a mesma estrutura de ID: para compras,
usar `numeroControlePNCP` quando dispon?vel e v?lido; para anexos, itens,
contratos e atas, aplicar as chaves pr?prias do recurso.

No c?digo final, impedir que uma resposta mais antiga ou incompleta substitua
uma mais recente, comparar hashes e definir ordena??o para empates de timestamps.
Persistir RAW e agendar o job de normaliza??o na mesma transa??o para evitar perda
de trabalho entre etapas. Se a normaliza??o falhar, o RAW fica recuper?vel.
N?o apagar vers?es anteriores quando uma chamada externa falhar.

## 9. Estados de execu??o

Jobs: `PENDING ? RUNNING ? SUCCEEDED`; falhas transit?rias v?o a `RETRY_WAIT`,
tentativas esgotadas a `FAILED`, inconsist?ncias estruturais a `QUARANTINED`.
Lease expirado devolve o job a `PENDING`; `FAILED` aceita replay autorizado;
`QUARANTINED` aceita replay ap?s corre??o validada.

`RUNNING ? PENDING` por lease expirado ? realizado pelo recuperador ap?s
verificar aus?ncia de heartbeat. Um mesmo `dedupe_key` n?o deve executar
concorrentemente, e o replay deve preservar o hist?rico de tentativas.

## 10. Observabilidade e opera??o

Indicadores m?nimos do documento:

| M?trica | Finalidade |
|---|---|
| ?ltimo sucesso e lag por fluxo | Atraso da sincroniza??o |
| Jobs pendentes, em execu??o, falhos e quarantined | Sa?de da fila PostgreSQL |
| Requisi??es/segundo, lat?ncia e erro por endpoint | Press?o e disponibilidade |
| P?ginas e registros por janela | Detectar volume at?pico ou lacunas |
| Tentativas e dura??o at? recupera??o | Avaliar retry e circuit breaker |
| Registros novos, atualizados e ignorados por hash | Conferir idempot?ncia |
| RAW pendente de normaliza??o | Desacoplamento e backlog |
| Enriquecimentos pendentes e alertas gerados | Sa?de da entrega |

O backend consome dados normalizados; n?o precisa consultar PNCP em cada request.

## 14. Checklist de aceite

- Novas contrata??es e atualiza??es seguem fluxos independentes.
- Janelas completas antes de avan?ar checkpoint; p?gina vazia v?lida n?o ? erro HTTP.
- Overlap/retry n?o duplica registros nem notifica??es.
- RAW ? armazenado antes da normaliza??o; falha n?o exige recoleta.
- Reserva concorrente, leases expirados, retry e circuit breaker t?m recupera??o segura.
- Sincroniza??o recente tem prioridade sem impedir progresso hist?rico.
- Atrasos, falhas, volume e qualidade ficam vis?veis em m?tricas.
- Enriquecimento segue crit?rios configurados; backend serve persistidos durante falhas.
- Nenhum servi?o RabbitMQ ? exigido.

## Rastreabilidade desta tarefa

`DOC-PNCP-06`, `DOC-PNCP-09`, `DOC-PNCP-10` e `DOC-PNCP-14` referenciam as
se??es 6, 9, 10 e 14 fornecidas pelo solicitante. A matriz de qualidade acrescenta
os crit?rios espec?ficos de normaliza??o p?s-ingest?o e evidencia sua valida??o.

Refer?ncias oficiais do documento:
[Swagger p?blico](https://pncp.gov.br/api/consulta/swagger-ui/index.html),
[manuais](https://www.gov.br/pncp/pt-br/pncp/manuais) e
[manual de integra??o](https://pncp.gov.br/manual/pt-br/latest/singlehtml/).
