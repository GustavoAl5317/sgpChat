# olt-wifi — o intermediário entre o bot e a OLT

Serviço mínimo que recebe *"troque o Wi-Fi da ONU X para o nome Y e a senha Z"*,
valida, e executa os dois comandos correspondentes na OLT por SSH.

## Por que ele existe

O n8n **consegue** abrir SSH sozinho. O motivo de não deixar é o tamanho do
estrago: a OLT controla a rede inteira do provedor, e o n8n é o processo que
conversa com o público pelo WhatsApp. Se a senha da OLT estivesse nele, um
comprometimento do bot viraria controle total da rede.

Com este serviço no meio, o bot só consegue pedir **esta** operação, com **estes**
parâmetros, nesta ONU. Não há caminho para executar outra coisa — os comandos são
montados aqui, a partir de campos validados, e nunca recebidos prontos.

## O que ele faz

```
POST /trocar-wifi
X-Token: <OLT_WIFI_TOKEN>

{"onu": "gpon_onu-1/2/2:1", "ssid": "Casa-do-Joao", "senha": "SenhaBoa123"}
```

Vira, na OLT:

```
configure terminal
pon-onu-mng gpon_onu-1/2/2:1
ssid ctrl wifi_0/1 name Casa-do-Joao
ssid auth wpa wifi_0/1 key SenhaBoa123
ssid ctrl wifi_0/5 name Casa-do-Joao
ssid auth wpa wifi_0/5 key SenhaBoa123
end
```

Responde `{"ok": true}` ou `{"ok": false, "detalhe": "<erro da OLT>"}`.

Campo omitido não vira comando: mandar só `ssid` troca só o nome.

`GET /saude` responde `{"ok": true}` sem tocar na OLT.

## Validação

Os valores vêm de alguém digitando no WhatsApp e terminam numa linha de comando
de switch. A validação é whitelist, nunca blacklist:

| campo | aceita |
|---|---|
| `onu` | `gpon_onu-<n>/<n>/<n>:<n>` e nada mais |
| `ssid` | 1-32 de `A-Z a-z 0-9 . _ -` |
| `senha` | 8-63 de alfanuméricos e símbolos comuns |

Dois caracteres merecem atenção especial no CLI da ZTE, e por isso não passam:

- **`?`** abre a ajuda contextual no meio da linha e quebra a sessão inteira
- **quebra de linha** encerra o comando e transforma o resto em comando novo

O bot aplica as mesmas regras na hora da digitação, para o cliente ser avisado
antes de escolher tudo — e não depois de confirmar.

## O que nunca vai para o log

A senha. Nem no pedido, nem na resposta de erro. O log registra a ONU, se o nome
mudou e se a senha mudou — nunca qual.

## Configuração

Tudo por variável de ambiente, no `.env` da raiz do projeto (ver `.env.example`):

| variável | para quê |
|---|---|
| `OLT_HOST`, `OLT_PORT`, `OLT_USER`, `OLT_PASS` | acesso à OLT |
| `OLT_WIFI_TOKEN` | token compartilhado com o n8n |
| `OLT_WIFI_IF_24`, `OLT_WIFI_IF_5G` | índices de SSID por banda |

Sobre `OLT_WIFI_IF_*`: nas ZTE testadas em campo, as SSIDs 1-4 são da rádio de
2.4 GHz e 5-8 da de 5 GHz — `wifi_0/1` e `wifi_0/5` são as principais de cada
banda. Só a primeira de cada uma é alterada: as outras são redes de visitantes,
e renomear a rede de visitantes de quem não pediu isso é mexer no que não foi
autorizado.

## Antes de ir ao ar

**1. Usuário dedicado na OLT.** Não use a conta de administração da equipe. Se a
OLT permitir autorização por comando, limite o usuário aos comandos de `ssid` —
vale perguntar ao fornecedor.

**2. Caminho de rede até a OLT.** Este serviço roda na mesma VM do bot, que é um
servidor na internet. A OLT normalmente só é alcançável de dentro da rede do
provedor. Antes de ativar o modo `olt`, resolva como essa conexão acontece:

- VPN entre a VM e a rede do provedor (preferível), ou
- acesso de gerência da OLT liberado apenas para o IP da VM, ou
- rodar este serviço dentro da rede do provedor, com o bot chamando por VPN

**Não exponha a gerência da OLT na internet sem restrição de origem.** É a caixa
que controla a rede inteira.

**3. Teste o caminho** antes de ligar o modo no bot:

```bash
docker compose up -d --build olt-wifi
docker compose logs -f olt-wifi
```

E, de dentro do n8n:

```bash
docker exec botsgp-n8n wget -qO- --header='X-Token: SEU_TOKEN' \
  --post-data='{"onu":"gpon_onu-1/2/2:1","ssid":"Teste-OLT"}' \
  --header='Content-Type: application/json' http://olt-wifi:8080/trocar-wifi
```

Use uma ONU de alguém da equipe. `{"ok":true}` e a rede mudando no local é o
único resultado que conta.
