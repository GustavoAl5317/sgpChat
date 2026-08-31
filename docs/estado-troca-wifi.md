# Troca de Wi-Fi — estado de campo (31/08/2026)

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
