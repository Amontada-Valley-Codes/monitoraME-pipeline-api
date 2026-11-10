# Pagamentos TCE-CE

## Fonte de verdade e escopo

O cálculo de pagamentos segue o projeto `dados`. O recorte financeiro validado
é composto por pagamentos de credores com CNPJ válido, vinculados a empenhos do
Executivo municipal e aos elementos `39`, `30`, `51`, `52`, `32`, `40` e `35`.
Pessoa física, documento inválido e Câmara não fazem parte desse recorte.

Ausência ou falha do cadastro empresarial não exclui um pagamento cujo CNPJ foi
identificado no empenho. Nesse caso, o valor permanece no controle financeiro,
o porte fica não identificado e a cobertura cadastral diminui.

## Medida financeira

- O valor é `valor_nota_pagamento`, armazenado e somado em centavos.
- `valor_empenhado` e empenho líquido não substituem valor pago.
- Somente `estado_de_estornado=0` entra no total.
- Estado diferente de `0` ou `1` invalida a análise do lote.
- O período é aplicado inclusivamente a `data_nota_pagamento`.
- O exercício orçamentário é independente do ano do pagamento. Assim, um
  empenho do orçamento de 2025 pode ter pagamentos até 2026.

As chaves são:

```text
empenho = município + exercício + órgão + unidade + data de emissão + número
pagamento = empenho + subempenho + número da nota de pagamento
```

Todos os componentes são textuais e preservam zeros à esquerda. Chaves
duplicadas conflitantes, pagamentos órfãos e dimensões conflitantes impedem a
publicação ou a análise; não são descartados silenciosamente.

## Coleta comprovadamente completa

O SIM limita respostas documentais a 1.000 registros. A paginação por
`$start_index` não foi considerada suficiente pelo projeto `dados`, pois sua
validação encontrou omissões. A coleta de pagamentos usa a mesma estratégia:

1. consulta a competência completa;
2. se atingir 1.000 registros, repete por órgão;
3. se um órgão atingir 1.000, repete por unidade orçamentária;
4. se uma unidade ainda atingir o limite, rejeita o lote;
5. confere que as subdivisões preservam os registros já observados.

A Câmara é identificada pelo cadastro de órgãos com
`codigo_tipo_unidade=02`. Órgão fora do cadastro e cadastro incompleto impedem
que o lote seja tratado como completo.

Após implantar esta versão, os lotes de pagamento existentes devem ser
reprocessados. Alterar apenas o código de leitura não corrige snapshots que
tenham sido coletados anteriormente por paginação simples.

## ME, MPE e cobertura

ME é somente o porte cadastral `ME`. MPE é calculada separadamente como ME +
EPP + MEI quando o cadastro indicar MEI. Um subtotal de ME nunca recebe o
rótulo MPE.

Quando algum CNPJ não possui classificação cadastral:

- o total financeiro continua definitivo;
- o subtotal identificado continua disponível;
- o total MPE definitivo fica nulo;
- a resposta informa valor e percentual cobertos pelo cadastro.

O porte é o retrato cadastral disponível, não uma comprovação histórica do
porte na data do pagamento.

## Rotas

`GET /pipeline/tce/pagamentos/overview` é a rota própria para valores pagos e
lê somente snapshots locais publicados. Ela não consulta TCE ou OpenCNPJ.

`GET /pipeline/tce/despesas` e `GET /pipeline/tce/overview` continuam medindo
empenho líquido. Seus campos e documentação devem usar essa denominação; esses
endpoints não podem ser usados como fonte de valor pago.

## Regressão de Acaraú

O caso de referência é município `004`, orçamento 2025, pagamentos de
2025-01-01 até 2026-09-30:

| Medida | Centavos |
|---|---:|
| Total pago líquido | 13.531.152.944 |
| Total pago a ME | 2.426.587.108 |
| ME — elemento 39 | 1.302.304.413 |
| ME — elemento 30 | 266.583.067 |
| ME — elemento 51 | 692.012.187 |
| ME — elemento 52 | 49.006.245 |
| ME — elemento 32 | 28.493.190 |
| ME — elemento 40 | 0 |
| ME — elemento 35 | 88.188.006 |

O ZIP de referência não contém a coleta bruta aprovada usada para produzir
esses números. Por isso, o teste offline real permanece pulado quando a fixture
não está presente. Uma fixture anonimizada ou sintética pode validar regras,
mas não pode ser usada para declarar que os totais reais acima foram
reproduzidos.
