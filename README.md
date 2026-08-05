# botSgp — Autoatendimento WhatsApp (Wi-Fi) via n8n + Evolution API + SGP

Primeiro módulo do bot: o cliente altera **nome (SSID) e senha do próprio
Wi-Fi** pelo WhatsApp, com validação de identidade. Serve de base para os
próximos módulos (financeiro, boleto, suporte) e para a migração futura
da Evolution API para a API oficial da Meta.

## Status

A integração com o SGP foi **verificada contra a API real** (base
`demo.sgp.net.br`): endpoints, autenticação, nomes de campo e formato das
respostas estão confirmados, não são suposições. O que falta é rodar
ponta a ponta com um contrato que tenha ACS/TR-069 de verdade — a base
demo não tem Gerenciador de CPE configurado em nenhum contrato.

## A API do SGP (verificado em campo)

Autenticação: `token` + `app` no corpo da requisição. O `app` é o
*appname* cadastrado junto com o token — se estiver errado, a resposta é
`{"detail":"Credenciais de autenticação incorretas."}` (403).

**Consultar cliente** — `POST /api/ura/consultacliente/`
```json
{ "app": "...", "token": "...", "cpfcnpj": "00000000000" }
```
Retorna `{ "msg": ..., "contratos": [ ... ] }`. Campos que o fluxo usa:

| Campo | Formato | Uso no bot |
|---|---|---|
| `contratoId` | int | id do contrato para o `cpemanage` |
| `contratoStatus` | int | **1=Ativo, 2=Inativo, 4=Suspenso** |
| `contratoStatusDisplay` | str | `"Ativo"` / `"Inativo"` / `"Suspenso"` |
| `razaoSocial` | str | nome do titular |
| `dataNascimento` | **`AAAA-MM-DD`** | segundo fator |
| `telefones[].contato` | `"(83) 98856-4565"` | confere o número do WhatsApp |
| `cpfCnpj` | `"000.000.000-00"` | vem formatado, com pontuação |

**Alterar Wi-Fi** — `POST /api/ura/cpemanage/` (aceita JSON ou form-urlencoded)
```
token, app, contrato          (obrigatórios)
servico                       (só se o contrato tiver múltiplos serviços)
wifi_status, novo_ssid, nova_senha
wifi_status_5, novo_ssid_5g, nova_senha_5g
```
Retorna `{"msg": ..., "success": true|false}`.

> Atenção: a documentação do Postman lista o parâmetro de 5GHz como
> `wifi_status_5g`, mas a própria API responde que o nome correto é
> **`wifi_status_5`**. O workflow usa o que a API aceita.

Existe também a família `/api/cpemanager/servico/{id_servico}/...`
(`wifi/set/`, `wifi/list/`, `reboot`, `ping`, `speedtest`). Ela usa
**Basic Auth** (usuário/senha do SGP) e indexa por *id do serviço*, não
por contrato. Preferi a `/api/ura/cpemanage/` no bot: mesma credencial
que a consulta de cliente, e indexada por `contratoId`, que já vem da
consulta.

## Fluxo da conversa

```
Evolution API (webhook)
   │
   ▼
Extract Inbound ──► Get Session ──► Parse & Route ──► Precisa chamar o SGP?
   normaliza         (Postgres)      máquina de          │
   o payload          estado da       estados            ├─► SGP Consultar Cliente ─► Processar Consulta CPF
                      conversa                           ├─► SGP Definir Wifi ──────► Processar Definir Wifi
                                                         └─► (nenhuma chamada)
                                                                    │
                              Preparar Persistência ◄───────────────┘
                                       │
                              Upsert Session ─► Tem auditoria? ─► Gravar Auditoria
                                                                          │
                                                        Evolution: Enviar Resposta
```

Estados: `menu` → `awaiting_cpf` → (`awaiting_contract_choice`) →
(`awaiting_second_factor`) → `awaiting_ssid` → `awaiting_password` → `menu`.
Digitar `menu`, `0`, `voltar` ou `sair` reinicia a qualquer momento.

O estado fica no Postgres (tabela `wa_sessions`) porque cada mensagem do
WhatsApp dispara uma execução independente do workflow — não há memória
entre elas.

## Validação de identidade (dois fatores)

CPF não é segredo — vazou em diversos incidentes no Brasil. Por isso o
CPF sozinho não libera a alteração:

1. **CPF/CNPJ** com verificação de dígito (aceita os dois; contratos PJ
   existem). 3 erros → atendimento humano.
2. **Conferência silenciosa do telefone**: compara os últimos 8 dígitos
   do número que está falando com os telefones do cadastro. Se bater, o
   cliente segue sem fricção extra.
3. **Segundo fator explícito** quando o número *não* bate: pede a data de
   nascimento do titular. 3 erros → atendimento humano.
4. Se o SGP não tiver telefone nem data de nascimento do cliente, o bot
   **não decide sozinho** — encaminha para atendimento humano.

O SGP entrega `dataNascimento` em ISO (`AAAA-MM-DD`) e o cliente digita
`DD/MM/AAAA`; o workflow normaliza os dois antes de comparar.

## Subir no servidor

Testado em Ubuntu 24.04. **Pré-requisitos:**

- Um domínio apontando para o IP do servidor. Sem domínio próprio dá para
  usar `SEU.IP.AQUI.nip.io`, que resolve sozinho e aceita certificado.
- Um proxy reverso já rodando (Traefik, via EasyPanel ou avulso). Este
  stack **não sobe proxy próprio** — ele se pendura no que já existe, para
  não disputar as portas 80/443.

```bash
git clone https://github.com/GustavoAl5317/sgpChat.git && cd sgpChat
bash install.sh
```

O `install.sh` instala Docker (se faltar), detecta a rede do Traefik,
gera o `.env` com senhas aleatórias, configura o firewall e sobe o
stack. Depois:

```bash
bash conectar-whatsapp.sh
```

Isso cria a instância na Evolution e mostra o QR Code no terminal para
parear o WhatsApp.

Por fim, no n8n (`https://SEU_DOMINIO`): importe
[n8n/workflow-wifi-selfservice.json](n8n/workflow-wifi-selfservice.json),
crie a credencial Postgres (host `postgres`, porta `5432`) e associe aos
três nodes de banco — eles vêm com o placeholder `REPLACE_ME`. Ative o
workflow e mande `1` para o número conectado.

### O que fica exposto na internet

Nada deste stack publica porta. O n8n entra na rede do Traefik que já roda
no servidor, e é ele quem expõe na 443. Postgres, Redis e Evolution
API **não têm porta publicada** — conversam apenas pela rede interna do
Docker. A Evolution chama o webhook do n8n em `http://n8n:5678`, sem sair
para a internet.

## Arquivos

| Arquivo | Papel |
|---|---|
| [docker-compose.yml](docker-compose.yml) | Stack: Postgres, Redis, n8n, Evolution |
| [install.sh](install.sh) | Provisiona o Ubuntu do zero |
| [conectar-whatsapp.sh](conectar-whatsapp.sh) | Cria a instância e mostra o QR Code |
| [sql/schema.sql](sql/schema.sql) | `wa_sessions` + `wa_wifi_change_log` |
| [build-workflow.py](build-workflow.py) | **Gera** o JSON do workflow |
| [n8n/workflow-wifi-selfservice.json](n8n/workflow-wifi-selfservice.json) | Workflow (gerado — não edite à mão) |
| [test-fluxo.js](test-fluxo.js) | Testa a máquina de estados |

### Mexendo no fluxo

O JSON do n8n é **gerado**. Edite o JS em `build-workflow.py` e rode:

```bash
python3 build-workflow.py && node test-fluxo.js resposta-sgp.json
```

`test-fluxo.js` roda os Code nodes fora do n8n contra uma resposta real
da API (20 casos: contrato múltiplo, 2FA, bloqueios, roteador sem ACS).
Editar o JSON à mão faz o `build-workflow.py` sobrescrever suas mudanças.

Para gerar o `resposta-sgp.json` do seu ambiente:

```bash
curl -s -X POST "$SGP_API_URL/api/ura/consultacliente/" -H "Content-Type: application/json" -d "{\"app\":\"$SGP_APP_NAME\",\"token\":\"$SGP_API_TOKEN\",\"cpfcnpj\":\"CPF_DE_TESTE\"}" -o resposta-sgp.json
```

## Pendências e riscos conhecidos

- **Não testado contra ACS real.** Toda a base demo responde
  `"O Serviço de internet não possui Gerenciador de CPE configurado."`.
  O caminho de sucesso (`success: true`) foi exercitado só com resposta
  simulada. O primeiro teste com hardware real é obrigatório antes de
  liberar para clientes.
- **2.4GHz e 5GHz recebem o mesmo nome e senha.** É o comportamento que a
  maioria dos provedores adota (band steering). Se você quiser sufixo
  `-5G`, mude no node `SGP - Definir Wifi`.
- **Contrato com múltiplos serviços**: o parâmetro `servico` do
  `cpemanage` não é preenchido. Se algum contrato tiver mais de um
  serviço de internet, o SGP pode não saber qual alterar.
- **Sem rate limit por telefone.** O bot corta em 3 tentativas de CPF,
  mas nada impede alguém de reiniciar o fluxo indefinidamente. Antes de
  abrir para a base toda, ponha um limite por número.
- **LGPD**: CPF e data de nascimento passam pelas execuções do n8n. O
  `EXECUTIONS_DATA_MAX_AGE` está em 168h (7 dias) para não acumular
  indefinidamente; o histórico de alterações fica em `wa_wifi_change_log`.
- **Migração para a Meta**: só o node `Extract Inbound` e o de envio
  mudam — o payload da Cloud API tem outro formato. O resto do fluxo é
  agnóstico ao transporte.

## Próximos módulos

Financeiro / 2ª via de boleto, abertura de chamado, e handoff real para
atendente (hoje o `human_handoff` só marca o estado; falta integrar com
uma fila — Chatwoot ou a central do próprio SGP).
