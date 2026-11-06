# M4 — RIPD (Relatório de Impacto à Proteção de Dados — LGPD Art. 38) — RASCUNHO

> **Rascunho técnico.** Escrito pela equipe M4 a partir do código e do [ROPA](m4-ropa.md). Antes de qualquer uso fora do hackathon, o RIPD precisa ser revisado e assinado pelo controlador e pelo encarregado (DPO). Os campos **[a definir pelo controlador]** dependem de decisões jurídicas que a equipe técnica não pode tomar.

---

## 1. Por que este tratamento pede um RIPD

O Art. 38 permite à ANPD exigir o relatório quando o tratamento pode gerar riscos às liberdades civis e aos direitos fundamentais. Neste sistema:

- **Dados financeiros detalhados** (dívidas, saldos, parcelas, CET) e **dados de crédito** (faixa de score e restrições do Serasa) de pessoas físicas.
- **Perfilamento:** o sistema classifica e prioriza as dívidas do titular e recomenda um plano de quitação (Art. 20).
- **Combinação de fontes:** Open Finance (várias instituições) e bureau de crédito.
- **Uso de modelo de IA generativa** (watsonx.ai Granite) para a explicação em linguagem simples.
- **Transferência internacional:** hospedagem em IBM Cloud us-south (Dallas, EUA).

## 2. Descrição do tratamento

| Item | Descrição |
|---|---|
| Controlador | **[a definir pelo controlador]** (cenário do desafio: Banco do Brasil) |
| Encarregado (DPO) | **[a definir pelo controlador]** |
| Operadores | IBM Cloud (Cloudant, COS, watsonx.ai); Serasa Experian como fonte de dados — ver [ROPA §3](m4-ropa.md) |
| Finalidade | Consolidar as dívidas do titular, priorizá-las por CET e recomendar um plano de quitação |
| Base legal | Consentimento (Art. 7º, I), no fluxo Open Finance; obrigação legal (Art. 7º, II) para a trilha de auditoria |
| Ciclo de vida | Consentimento (M1) → coleta e snapshot pseudonimizado (M2) → simulação e recomendação (M3) → retenção, apagamento e auditoria (M4) |
| Volume | **[a definir pelo controlador]** |

Mapeamento campo a campo: [`m4-mapeamento-dados.md`](m4-mapeamento-dados.md).

## 3. Necessidade e proporcionalidade

| Princípio (Art. 6º) | Como o sistema atende |
|---|---|
| Finalidade e adequação | Dados usados só para o plano de quitação; o consentimento lista instituições e escopos |
| Necessidade (minimização) | CPF nunca persistido em claro (HMAC + cofre cifrado); faixa de score em vez do score exato; número de contrato substituído por referência HMAC; guarda de PII bloqueia, antes de qualquer gravação, CPF em qualquer valor ou chave e campos com nome de dado pessoal (`nome`, `email`, `telefone`, `endereco`, `contract_id` etc.) |
| Livre acesso e transparência | Exportação completa por `POST /internal/subject/export`; explicação da recomendação em linguagem simples |
| Qualidade dos dados | Dados vêm direto das fontes autorizadas; correção feita na origem com novo consentimento |
| Segurança e prevenção | Ver seção 5 |
| Não discriminação | A recomendação é escolhida por regra determinística sobre os números da simulação ([ADR 0001](adr/0001-recomendacao-por-regra-deterministica.md)); o modelo não decide, só explica |
| Responsabilização | Trilha de auditoria de toda operação sobre dados do titular, com eventos fechados e sem PII |

## 4. Riscos identificados

Escala: probabilidade e impacto em Baixo / Médio / Alto, avaliados para o estado atual do código.

| # | Risco | Prob. | Impacto | Medidas existentes | Risco residual |
|---|---|---|---|---|---|
| R1 | Re-identificação do titular a partir dos dados pseudonimizados | Baixa | Alto | HMAC com chave segregada; CPF só no cofre cifrado (AES-256-GCM, DEK por titular) | Médio enquanto a chave estiver em variável de ambiente (sem HSM) |
| R2 | Uso do CPF fora da finalidade | Baixa | Alto | `reveal()` auditado; finalidade amarrada ao serviço; consentimento ativo exigido para o Serasa; falha fechada | Baixo; o controle de actor não resiste a um atacante com execução de código no servidor |
| R3 | Vazamento de PII em logs, trilha ou respostas de erro | Média | Alto | Guarda de PII antes de gravar; erros não ecoam valores; CPF só no corpo das requisições; rota sem parâmetros nos logs de 401; CPF mascarado no access log do servidor | Baixo |
| R4 | Retenção além do necessário | Média | Médio | Job de expurgo idempotente com agendador; regras por tipo de documento | Médio: o agendador vem desligado por padrão e o prazo da trilha de auditoria ainda não foi definido |
| R5 | Apagamento incompleto após rotação de chave | Baixa | Alto | Apagamento e exportação por CPF em todas as versões da chave | Baixo, desde que as chaves antigas não sejam descartadas antes da migração |
| R6 | Cópias do CPF cifrado em backups e revisões do Cloudant após o apagamento | Média | Médio | Crypto-shredding do documento corrente | Médio: cópias antigas seguem decifráveis enquanto a KEK global existir; em produção, DEK por titular em HSM destruída no shred |
| R7 | Decisão automatizada desfavorável sem revisão | Média | Médio | Regra determinística e explicável; exportação inclui decisão e explicação | Médio: o canal de revisão por pessoa (Art. 20) não está definido |
| R8 | Conteúdo indevido gerado pelo modelo (valores inventados, orientação errada) | Média | Médio | Guarda de valores na explicação (M3); prompt e saída guardados com hashes | Baixo |
| R9 | Acesso indevido aos endpoints internos | Baixa | Alto | Token interno comparado em tempo constante; 401 registrado como `internal.auth_failed` | Médio: token único sem rotação automática e alertas não ativos nesta conta |
| R10 | Transferência internacional sem garantia contratual | **[a definir]** | Alto | Hospedagem em provedor com DPA | **[a definir pelo controlador]**: cláusulas-padrão (Art. 33) |
| R11 | Adulteração da trilha de auditoria | Baixa | Médio | Append-only pela aplicação; cópia no COS | Médio: sem WORM, hash chain nem credencial exclusiva de escrita nesta conta |

## 5. Medidas de mitigação

Implementadas: ver [ROPA §5](m4-ropa.md) e [`m4-chaves-e-iam.md`](m4-chaves-e-iam.md).

Pendentes, em ordem de prioridade:

1. **Definir os prazos** de guarda da trilha de auditoria e de atendimento à eliminação; configurar `AUDIT_RETENTION_DAYS` e a expiração do bucket COS.
2. **Ligar a retenção periódica** no ambiente da apresentação (`RETENTION_SCHEDULE_ENABLED=true`) ou, em produção, criar um job agendado único.
3. **Definir o canal de revisão humana** das recomendações (Art. 20).
4. **Chaves em HSM** (Secrets Manager / Key Protect) e Trusted Profile em vez de API key; DEK por titular no HSM para que o crypto-shredding alcance os backups.
5. **Integridade da trilha:** Object Lock no COS, hash chain entre eventos, credencial só de escrita.
6. **Alertas** sobre os logs `desendividai.audit` no IBM Cloud Logs ([`m4-auditoria-governanca.md`](m4-auditoria-governanca.md) §8).
7. **Envio do trace ao watsonx.governance** (AI Factsheets) para a governança do modelo.
8. **Revogação do token OAuth** no apagamento e na expiração, quando o contrato do API Connect existir.

## 6. Conclusão (preliminar)

Do ponto de vista técnico, os riscos altos de re-identificação, vazamento de PII e apagamento incompleto têm controles implementados e testados. Os riscos residuais médios dependem principalmente de **decisões do controlador** (prazos, revisão humana, transferência internacional) e de **serviços que a conta do hackathon não oferece** (HSM, Cloud Logs, WORM).

**Parecer do encarregado:** **[a definir pelo controlador]**

## 7. Histórico

| Versão | Data | Autor | Descrição |
|---|---|---|---|
| 0.1 | 2026-11-06 | Equipe M4 | Rascunho técnico inicial |
