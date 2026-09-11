# Troca de Wi-Fi — estado de campo (31/08/2026)

> **Leia antes:** este documento tem duas partes. A de cima é de 31/08/2026 e
> várias conclusões dela foram **derrubadas em campo**. A parte de baixo,
> *Atualização de campo — 04/09/2026*, é a que vale. Ela diz exatamente o que
> mudou e por quê.

Registro do que foi descoberto e alterado em campo no dia 31/08/2026, quando o
projeto saiu do papel e foi testado na rede real.

**A conclusão que muda tudo:** a troca de Wi-Fi **funciona pela OLT**, por OMCI,
sem servidor ACS. Foi provado num equipamento real. O caminho TR-069, no qual o
projeto vinha sendo construído, continua travado — e provavelmente deixou de ser
necessário.

---

## O que foi provado funcionando

Na OLT ZTE, no contexto da ONU:

```
configure terminal
pon-onu-mng gpon_onu-1/2/2:1
ssid ctrl wifi_0/1 name NomeDaRede
ssid auth wpa wifi_0/1 key SenhaDaRede
```

O nome mudou na lista de redes do celular do assinante em segundos. Sem ACS, sem
TR-069, sem VLAN de gerência, sem DHCP e sem tocar no switch.

Limites do identificador, conforme a própria OLT:

| campo | faixa |
|---|---|
| `name` | 1-32 caracteres |
| `key` | 8-63 caracteres |

São **exatamente** as faixas que o bot já valida hoje. A validação da máquina de
estados não precisa mudar para este caminho.

### O bloqueio deste caminho: só 2.4 GHz

Apenas `wifi_0/1` existe. Os índices `0/2`, `0/3`, `0/4` e `0/5` respondem
`%Error 223845: UNI does not exist`. Ou seja, **a rede de 5 GHz não é alcançável
por OMCI** nessa ONU.

Isso não é detalhe. O assinante pede troca de senha, o bot troca só a de 2.4 GHz,
e os aparelhos de 5 GHz continuam na senha antiga — "mudou e não mudou", o pior
tipo de falha para o suporte. O bot recusa alterações parciais de propósito,
então hoje ele não aplicaria.

**Causa provável:** o `onu-type` da ONU é `F670L`, mas o aparelho é um
**F6600P**. O perfil declara à OLT quais portas existem, e o dele declara uma
porta Wi-Fi só.

Isso conecta com o achado do inventário (abaixo): há ONUs em massa com o
`onu-type` trocado. **Resolver o `onu-type` é o próximo passo do projeto.**

---

## O parque (levantado pelo SGP, sem tocar na OLT)

`bash levantar-parque.sh` — 409 ONUs, uma OLT.

| corte | resultado |
|---|---|
| Fabricante (serial GPON) | **Huawei 346 (84,6%)**, ZTE 63 (15,4%) |
| `onu-type` configurado | F670L 183, hg8145v5-v2 131, hg8145v5 95 |
| Modo | PPPoE 372 (91%), **Bridge 37 (9%)** |

**Divergência importante:** 183 ONUs estão com `onu-type` F670L (perfil ZTE), mas
só 63 são ZTE de verdade. Cerca de **120 ONTs Huawei rodam com perfil de ZTE**.

Os **37 em bridge** nunca entram na automação: o Wi-Fi que o assinante usa é de
um roteador separado, fora do alcance da OLT e de qualquer ACS. Ficam no modo
`chamado` em definitivo.

---

## O desenho da rede (levantado em campo)

```
ONU  ──PON──  OLT ZTE C6xx  ──xgei-1/10/1──  SWITCH  ──Te0/3/0──  ASR1000 (BNG)
                                                                      │
                                                                   Te0/2/0
                                                                      │
                                                                  internet
```

- **OLT:** ZTE série C6xx (cartões SFUQ + HFTH, software V5). Interfaces no
  formato `gpon_onu-1/<slot>/<pon>:<onu>`.
- **BNG:** Cisco ASR1000, IOS XE 15.5(3)S9. Uma subinterface por PON
  (`Te0/3/0.11` a `.32`), cada uma terminando PPPoE. CGNAT ativo — assinantes
  recebem `100.64.x.x` e saem pelo pool `143.202.8.168/29`.
- **VLAN 200:** vive **dentro da OLT**. Não existe subinterface `.200` no BNG.
- **Switch entre OLT e BNG:** existe. As VLANs de PON passam; a 600 nunca foi
  liberada nele.
- **SmartOLT:** a assinatura **não está mais ativa**. O perfil dele deixou
  resíduos na configuração (ver VLAN 600 abaixo), mas não é mais uma via.

### O mapeamento contrato → ONU já existe

`GET /api/fttx/onu/list/?contrato=` devolve `slot`, `pon` e `onuid`. Daí sai o
endereço da OLT direto:

```
gpon_onu-1/<slot>/<pon>:<onuid>
```

Verificado: contrato do teste → slot 2, pon 2, onu 1 → `gpon_onu-1/2/2:1`.
Nenhum cadastro novo é necessário para o bot achar o equipamento.

---

## O caminho TR-069 — onde parou

Tudo abaixo está **pronto e no ar**, e não funcionou:

| camada | estado |
|---|---|
| GenieACS | no ar, NBI respondendo, 7547 aberta e testada da internet |
| Bot | modo `genieacs` implementado, 139 testes passando |
| ASR1000 | subinterface VLAN 600, DHCP, NAT restrito ao IP do ACS |
| OLT | VLAN 600 no uplink, gemport 2, service com VEIP, TR-069 com URL do ACS |
| ONU | `Config state: success` — aceitou toda a configuração |

E mesmo assim: `show mac vlan 600` = **0**. A ONT nunca criou a interface de
gerência, nunca pediu DHCP, nunca falou com o ACS. A tela da ONT segue mostrando
só a conexão de internet.

Configuração final da ONU no teste:

```
service vlan200 gemport 1 vlan 200
service mgmt gemport 2 veip 1 vlan 600
veip 1
tr069-mgmt 1 state unlock acs http://179.197.226.140:7547/ tag pri 0 vlan 600
```

E na interface:

```
tcont 1 profile SMARTOLT-1G-UP
gemport 1 tcont 1
gemport 2 tcont 1
```

**Dois bloqueios conhecidos e ainda abertos neste caminho:**

1. A ONT não cria a WAN de gerência (motivo desconhecido — é comportamento do
   firmware sobre o provisionamento OMCI).
2. O switch entre a OLT e o BNG não passa a VLAN 600. Mesmo resolvendo (1), o
   tráfego morreria ali.

`mgmt-ip` na OLT só aceita IP fixo (`A.B.C.D`), não DHCP — o que tornaria esse
caminho inviável para 409 equipamentos mesmo que funcionasse.

### A VLAN 600 é órfã

Ela já existia na OLT e nas ONUs antes deste projeto, com `tr069-mgmt 1 state
unlock tag pri 0 vlan 600` e **sem URL de ACS**. Foi o SmartOLT que a criou, num
projeto de TR-069 começado e abandonado. Do lado da rede ela nunca foi
terminada: não existia no BNG e não passa no switch.

---

## O que foi alterado em produção (31/08/2026)

Registro para auditoria e reversão.

### Cisco ASR1000 — **salvo** (`write memory`)

```
interface TenGigabitEthernet0/3/0.600
 description GERENCIA-TR069-ONU
 encapsulation dot1Q 600
 ip address 10.200.0.1 255.255.252.0
 ip nat inside
!
ip dhcp excluded-address 10.200.0.1 10.200.0.20
!
ip dhcp pool ONU-TR069
 network 10.200.0.0 255.255.252.0
 default-router 10.200.0.1
 lease 7
!
ip access-list extended SERVIDOR
 25 permit ip 10.200.0.0 0.0.3.255 host 179.197.226.140
```

Backup anterior em `flash:pre-tr069-20260831.cfg`. Para reverter:

```
no interface TenGigabitEthernet0/3/0.600
no ip dhcp pool ONU-TR069
no ip dhcp excluded-address 10.200.0.1 10.200.0.20
ip access-list extended SERVIDOR
 no 25
```

Tudo aditivo — nenhuma configuração existente foi alterada.

### OLT ZTE — **não salvo** (`write` não foi executado)

Um reload da OLT limpa tudo isto:

- `gpon_onu-1/2/2:1` (ONU do teste): `gemport 2 tcont 1`, `service mgmt gemport 2
  veip 1 vlan 600`, `tr069-mgmt 1 ... acs ...`, e as alterações de SSID/senha do
  teste.
- `gpon_onu-1/2/2:2` (**ONU de outro assinante, configurada por engano**):
  restou `tr069-mgmt 1 state unlock`. A URL do ACS foi removida. É inofensivo —
  sem endereço, não há com quem falar — mas **deve ser limpo**. Essa ONU também
  levou um `reboot` (~1 min sem internet para aquele cliente).

> Como escolher a ONU: `show gpon onu by sn <SERIAL>` devolve a porta. O serial
> que a OLT entende é o **GPON SN** da etiqueta (começa com `ZTEG`/`HWTC`), não o
> "D-SN" de fabricação, que também aparece no rótulo.

---

## Se o bot passar a falar com a OLT

Desenho proposto, **ainda não implementado**:

1. **Contrato → ONU** pelo SGP (`slot`/`pon`/`onuid`), montando
   `gpon_onu-1/<slot>/<pon>:<onuid>`.
2. **Um serviço intermediário na VM**, não o n8n direto. Ele recebe "trocar
   Wi-Fi da ONU X para nome Y e senha Z", valida, monta **apenas** os dois
   comandos `ssid` e executa por SSH. As credenciais da OLT ficam nele.

   O motivo é simples: um comprometimento do bot não pode virar controle total
   da rede. Com o intermediário, o bot pede uma operação específica e não
   consegue executar mais nada na OLT.
3. **Usuário restrito na OLT** para esse serviço, se a ZTE permitir autorização
   por comando.

O `wa_wifi_change_log` que já existe serve para auditoria deste caminho sem
mudanças de esquema.

---

## Próximos passos, em ordem

1. **Resolver o 5 GHz.** É o que decide se este caminho vale a pena. Testar numa
   ONU cujo `onu-type` bata com o modelo, e olhar o que o perfil declara. Se for
   o `onu-type` trocado, corrigi-lo destrava as duas bandas — e possivelmente
   destrava também as ~120 Huawei com perfil de ZTE.
2. **Limpar a `gpon_onu-1/2/2:2`** e restaurar o Wi-Fi da ONU de teste.
3. **Testar numa ONU Huawei**, que é 85% do parque. O que foi provado hoje foi
   numa ZTE.
4. **Decidir o caminho** — OLT ou TR-069 — antes de escrever mais código.
5. Só então implementar o `WIFI_MODO=olt` e o serviço intermediário.

Enquanto isso, `WIFI_MODO=chamado` segue no ar e atendendo: o assinante se
identifica, escolhe o que quer, e a equipe recebe o pedido pronto.

---
---

# Atualização de campo — 04/09/2026

Um dia inteiro na rede real. Várias conclusões do dia 31/08 caíram.

## O 5 GHz está resolvido

A causa era a que o documento suspeitava: o `onu-type`. O perfil `F670L` declara
uma única porta Wi-Fi, e a OLT recusa qualquer índice que o perfil não declare.

Foi criado o perfil `RCNET-HGU`, idêntico ao `F670L` nos limites, mas declarando
`wifi_0/1` a `wifi_0/8` e com `ex-omci enable`. Com a ONU migrada para ele, os
comandos nas duas bandas passam a ser aceitos:

```
pon-onu-mng gpon_onu-1/2/2:1
ssid ctrl wifi_0/1 name NomeDaRede     <- 2.4 GHz
ssid auth wpa wifi_0/1 key SenhaDaRede
ssid ctrl wifi_0/5 name NomeDaRede     <- 5 GHz
ssid auth wpa wifi_0/5 key SenhaDaRede
```

Ou seja: **o caminho pela OLT entrega o produto completo**, nas duas bandas, sem
ACS. É o caminho principal do projeto.

## Migrar o `onu-type` não quebra o assinante — se a restauração for completa

A migração exige `no onu` e recriar, o que derruba a conexão por alguns minutos.
O que quebra de verdade é restaurar pela metade.

**Foi isso que tirou dois clientes do ar** nos testes. O sintoma na ONT é
"Motivo da desconexão: ISP Timeout", que engana: parece problema de PPPoE e é
falta do `service-port`, que traduz a VLAN do assinante para a do uplink e é
apagado junto com a ONU.

A fórmula da VLAN de uplink desta rede:

```
VLAN = 10 + (slot - 1) * 16 + pon
```

`1/1/1` -> 11, `1/1/2` -> 12, `1/2/2` -> 28.

### Receita completa de migração (validada, sem queda residual)

Antes de mexer, capture o que existe — nunca restaure de memória:

```
configure terminal
interface gpon_olt-1/2/2
show this
exit
interface gpon_onu-1/2/2:1
show this
exit
pon-onu-mng gpon_onu-1/2/2:1
show this
exit
interface vport-1/2/2.1:1
show this
exit
```

Depois:

```
configure terminal
interface gpon_olt-1/2/2
no onu 1
onu 1 type RCNET-HGU sn ZTEGDA11A47B
exit
interface gpon_onu-1/2/2:1
tcont 1 profile SMARTOLT-1G-UP
gemport 1 tcont 1
exit
pon-onu-mng gpon_onu-1/2/2:1
service vlan200 gemport 1 vlan 200
veip 1
exit
interface vport-1/2/2.1:1
service-port 1 user-vlan 200 vlan 28
qos traffic-policy SMARTOLT-1G-DOWN direction egress
exit
exit
write
```

Conferência — os três precisam estar certos:

```
show gpon onu detail-info gpon_onu-1/2/2:1    -> Config state: success
show service-port interface gpon_onu-1/2/2:1  -> Status OK / Enable YES
```

E no BNG, que a sessão voltou:

```
show pppoe session | include <mac do assinante>
```

### O `ex-omci` não pode ser ligado num tipo em uso

```
onu-type F670L gpon ... ex-omci enable
%Error 230296: Profile is being used.
```

Não há atalho: para mudar as características do perfil é preciso migrar as ONUs
para um perfil novo, uma a uma.

## TR-069: o que funciona e o que não funciona

| caminho | resultado |
|---|---|
| Configurado **na interface do aparelho** | **funciona** — registrou no GenieACS e respondeu `GetParameterValues` |
| Provisionado **pela OLT** (`tr069-mgmt` por OMCI) | **não funciona** neste firmware |

Pela OLT, mesmo com `ex-omci enable`, `mgmt-ip` entregue (a ONT aparece em
`show mac vlan 600`), `tr069-mgmt` gravado e o ACS acessível da internet, o
aparelho nunca abre sessão CWMP.

Como configurar na interface exige acesso ao aparelho de cada assinante, o
TR-069 fica como **caminho para instalações novas**, não para a base existente.

### Conclusões do dia 31/08 que caíram

- **"A configuração manual de TR-069 não funcionou."** Funcionou. O servidor do
  ACS estava fora do ar naquele momento e ficou fora o dia todo. O aparelho
  registrou assim que o servidor voltou.
- **"`Extended OMCI: disable` impede o `mgmt-ip` de chegar."** Não impede — a
  ONT recebeu o IP de gerência e apareceu na VLAN 600 com o perfil antigo.
- **"O TR-069 precisa da VLAN de gerência."** Não precisa. O aparelho que
  registrou chegou ao ACS pela internet do próprio assinante, saindo pelo CGNAT.
  A VLAN 600, o `mgmt-ip` e o service-port de gerência não são requisito.

## Huawei nao aceita Wi-Fi por OMCI — testado, e a resposta e nao

Medido em 04/09/2026 na ONU `gpon_onu-1/1/1:19` (`HWTC611D44AC`, Huawei em
bridge, sem contrato vinculado):

| passo | `Config state` |
|---|---|
| perfil original `F670L` | `success` |
| migrada para `RCNET-HW` | `success` |
| `ssid ctrl wifi_0/1 name TesteHuawei` | **`fail`** |
| recriada limpa em `RCNET-HW` | `success` |
| `ssid ctrl wifi_0/1` sozinho, de novo | **`fail`** |

Duas coisas ficam separadas por esse teste:

- **A migração de perfil funciona em Huawei.** O aparelho aceitou um perfil que
  declara oito portas de Wi-Fi e uma de telefone que ele não tem.
- **O comando de Wi-Fi não funciona.** A OLT aceita no CLI, manda por OMCI, e a
  ONT rejeita. Nem 2.4 GHz. O índice também não é questão de mapeamento: foi
  testado o `wifi_0/1` isolado, que é o primeiro de qualquer fabricante.

A explicação é que `ssid ctrl` e `ssid auth` são extensões proprietárias da ZTE.
Equipamento ZTE entende; Huawei não tem obrigação de entender, e não entende.

A ONU do teste foi devolvida ao perfil `F670L` original.

### O que isso faz com a cobertura

| | ONUs | automação pela OLT |
|---|---|---|
| ZTE (serial `ZTEG`) | 65 | **sim** |
| Huawei (serial `HWTC`) | 348 | não |

Cerca de **16% da base** pode ter a senha do Wi-Fi trocada pelo bot sozinho. O
resto continua atendido — o bot valida a identidade, coleta o pedido e abre
chamado no SGP com a senha escolhida — mas depende de alguém aplicar.

A conferência de perfil do `olt-wifi` já produz exatamente esse comportamento:
ONU em perfil antigo é recusada sem nada ter sido tocado, e o pedido vira
chamado. Nenhuma mudança de código é necessária por causa deste resultado.

### O que restaria tentar para a Huawei

TR-069 configurado **no aparelho** funciona — foi assim que o F6600P do Ygson
registrou no ACS e obedeceu a um `SetParameterValues`. Mas a habilitação é um a
um, na interface web de cada ONT, e por isso não serve para migrar a base. Serve
para instalação nova, onde o técnico já está com o aparelho na mão.

## Pendências abertas

- **Contrato 466** (`gpon_onu-1/1/1:4`, `ZTEGD420E6A8`) segue fora do ar por
  falta de `service-port`. Restaurar com `service-port 1 user-vlan 200 vlan 11`
  e a política de QoS de egresso.
- Limpar sobras dos testes: `tr069-mgmt` e `security-mgmt` deixados em
  `gpon_onu-1/2/2:2` e em `souza073` (`gpon_onu-1/1/2:19`).
- Remover o preset padrão do GenieACS que tenta escrever
  `ManagementServer.PeriodicInformTime` — o firmware recusa com `9007` e suja
  todas as sessões.

## A migração da base

O gerador existe: `migrar-perfil.py`. Ele não toca na OLT — lê a configuração e
escreve os comandos, que você confere antes de colar.

```bash
ssh admin@olt 'show running-config' > running-config.txt
python3 migrar-perfil.py running-config.txt --pon 1/2 > migrar-pon-1-2.txt
```

Ele lê a configuração **real** de cada ONU e devolve a restauração completa, na
ordem certa. Se encontrar uma linha que não sabe recolocar, pula a ONU e diz por
quê — uma ONU não migrada é melhor que uma ONU migrada pela metade.

O `--pon` existe porque migrar de PON em PON limita o tamanho de qualquer erro.
Ao fim de cada bloco há duas linhas de `show` para conferir; se um `Sport` não
voltar em `OK/YES`, pare, porque é exatamente o que deixa o assinante com
"ISP Timeout".

Uma ressalva: a linha `mgmt-ip` do `running-config` termina com um `host 1` que
a própria OLT acrescenta. Se ela recusar esse sufixo na hora de recolocar, tire
o `host 1` e siga. Só afeta ONUs com gerência configurada, que hoje é uma só.

---
---

# Atualização de campo — 11/09/2026: Huawei ENTRA, pela WAN TR069_INTERNET

Caiu a conclusão de que Huawei nao tinha caminho. Tem — pelo TR-069, nao pela
OLT. Provado na ONU da Amanda (HG8145V5, contrato 316, `gpon_onu-1/2/2:3`).

## O que destrava

A Huawei nao emite TR-069 com a WAN de servico so `INTERNET`. Criando no
aparelho uma WAN de servico **`TR069_INTERNET`** (PPPoE, VLAN 200, mesmo login
do cliente, SSID1+SSID5 marcados), a ONT registra no ACS em segundos. Confirmado
por tcpdump: sessao CWMP completa, e o GenieACS leu e escreveu sem erro.

Feito isso, a Huawei entrega TUDO: expoe `WLANConfiguration.1` (2.4 GHz) e
`.5` (5 GHz), e aceita a troca de senha nas duas.

## O parametro de senha da Huawei

Aqui estava um erro no bot. A senha foi testada por caminho:

| parametro | resultado |
|---|---|
| `WLANConfiguration.N.KeyPassphrase` | **recusado** (fault 9002, derruba a tarefa inteira) |
| `WLANConfiguration.N.PreSharedKey.1.KeyPassphrase` | **aceito** |

O `setParameterValues` e atomico: um parametro ruim derruba todos. O bot mandava
o `KeyPassphrase` do topo (que aparece escrivel no modelo e mesmo assim derruba)
junto com o do PreSharedKey. Corrigido: quando existe `PreSharedKey.1`, a senha
vai SO por ele; o `KeyPassphrase` do topo so entra em firmware antigo que nao
tem PreSharedKey nenhum.

## O procedimento correto da WAN — UMA, nao duas

O primeiro teste criou uma SEGUNDA WAN (TR069_INTERNET) ao lado da de internet.
Erro: as duas discam PPPoE com o mesmo login e viram **duas sessoes**, o que
quebra a navegacao (so sites grandes abrem - sintoma de MTU/rota). 

O certo e ter UMA WAN so, de servico `TR069_INTERNET`, que faz internet E
gerencia na mesma sessao. Na pratica, na instalacao/visita:

1. apaga a WAN `INTERNET` existente
2. cria uma nova, servico `TR069_INTERNET`, resto igual (PPPoE, VLAN 200,
   login do cliente, bind SSID1+SSID5)
3. Apply

Uma sessao, os dois papeis, sem duplicar.

## Como fica a cobertura

Com isto, as ~348 Huawei passam a ter caminho - mas cada uma precisa da WAN
`TR069_INTERNET` criada NO APARELHO (nao ha como fazer pela OLT, ja testado). E
trabalho de instalacao/visita, um toque por aparelho, e dali em diante o bot
troca a senha sozinho pelo ACS, nas duas bandas. O parque Huawei vira por
demanda, puxado pelos chamados e instalacoes.

Resumo do parque:

| | como troca a senha | pre-requisito |
|---|---|---|
| ZTE (65) | pela OLT, na hora | migrar perfil F670L -> RCNET-HGU |
| Huawei (348) | pelo ACS (TR-069), ~2 min | criar WAN TR069_INTERNET no aparelho |

## Pendente

- Confirmar no celular da Amanda que a senha nova conecta (o aparelho foi
  desligado antes do teste final).
- Limpar os residuos de teste da ONU do Ygson (`gpon_onu-1/2/2:1`): VLAN 600,
  mgmt-ip, tr069-mgmt, service-port 2, e o Wi-Fi que ficou com nome de teste
  `RCNet-Teste5G`. So quando ela voltar a ficar online.
