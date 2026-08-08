# Piloto: GenieACS para a troca de Wi-Fi

O SGP da RCNet nao tem Gerenciador de CPE cadastrado (`0 Gerenciadores de CPE`
em Sistema -> Gerenciador de CPE), e por isso `ura/cpemanage` responde
`O Servico de internet nao possui Gerenciador de CPE configurado` em toda
chamada. Sem um servidor ACS nao existe caminho ate o roteador do cliente, e
nenhuma mudanca no bot resolve.

O dropdown do SGP aceita **GenieACS (TR-069)**, que e open source e
self-hosted - da para provar o caminho inteiro sem contratar nada.

## O que este piloto prova, e o que nao prova

**Prova:** que `bot -> SGP -> GenieACS -> ONU` funciona de ponta a ponta, e
quanto tempo a alteracao leva para aparecer no celular.

**Nao prova:** que a base inteira funciona. Isso depende de provisionar todas
as ONUs apontando para o ACS, o que e trabalho na OLT e decisao da RCNet.

**Nao e producao.** Em producao o ACS tem acesso administrativo ao roteador de
todos os assinantes. Antes disso: TLS no CWMP, credencial por equipamento, NBI
fechada para a internet, backup do Mongo e um dono definido dentro da RCNet.
Nada disso esta aqui, e nao deve estar - piloto que vira producao por inercia e
como esse tipo de sistema costuma vazar.

## Subir

```bash
cd genieacs
cp .env.example .env
sed -i "s/troque-por-um-valor-aleatorio/$(openssl rand -hex 32)/" .env
docker compose up -d --build
docker compose logs -f
```

A interface fica em `http://<ip-do-servidor>:3000`. O primeiro acesso pede para
criar o usuario admin.

Confira que os quatro servicos responderam:

```bash
curl -s -o /dev/null -w 'NBI %{http_code}\n' http://localhost:7557/devices
curl -s -o /dev/null -w 'UI  %{http_code}\n' http://localhost:3000/
```

## Apontar UMA ONU para o ACS

Esta parte e da RCNet - precisa de acesso a OLT, e a ONU tem que ser de alguem
da equipe, nunca de cliente.

O que o tecnico precisa configurar na ONU (ou no perfil dela na OLT):

| parametro TR-069                              | valor                              |
|-----------------------------------------------|------------------------------------|
| `ManagementServer.URL`                        | `http://<ip-publico>:7547/`        |
| `ManagementServer.PeriodicInformEnable`       | `true`                             |
| `ManagementServer.PeriodicInformInterval`     | `300` (5 min, so no piloto)        |
| `ManagementServer.Username` / `Password`      | opcional no piloto                 |

O `<ip-publico>` precisa ser alcancavel da rede onde a ONU esta. Se o servidor
estiver atras de NAT, libere a 7547.

Em poucos minutos o equipamento aparece em **Devices** na interface do
GenieACS. Se nao aparecer, o problema esta no caminho ONU -> 7547, nao no
GenieACS: confira firewall e se a ONU tem TR-069 habilitado no firmware.

## Cadastrar no SGP

Sistema -> Gerenciador de CPE -> Adicionar:

- **Nome:** GenieACS (TR-069)
- **Descricao:** algo que identifique que e piloto
- **Parametros JSON:** aqui ha uma incerteza que vale saber antes de tentar.
  O campo mostra um exemplo com `flashman.anlix.io` e **ele nao muda ao
  escolher GenieACS no dropdown** - e placeholder estatico, nao template por
  gerenciador. O SGP nao documenta na tela o que espera para GenieACS.

  O palpite mais provavel, pela forma do exemplo, e a mesma estrutura com a
  URL apontando para a **NBI** (porta 7557, nao a 7547 do CWMP):

  ```json
  {"url": "http://IP-DO-SERVIDOR:7557", "username": "", "password": "",
   "ping_hosts": ["www.google.com", "www.youtube.com"]}
  ```

  A NBI do GenieACS nao tem autenticacao propria, entao username/password
  provavelmente ficam vazios ou correspondem a um basic auth posto na frente
  por proxy.

  Se nao funcionar, nao insista adivinhando: pergunte ao suporte da TSMX qual
  JSON o SGP espera para GenieACS. Eles respondem isso em minutos, e cada
  tentativa cega aqui custa uma rodada de teste com o tecnico parado.
- Marque **Central definir Wifi** se quiser que a Central do Assinante tambem
  ofereca a troca.

## Testar

Primeiro fora do bot, para isolar:

```bash
cd ~/sgpChat/sgpChat
bash testar-wifi.sh --cpf <cpf-do-funcionario>
bash testar-wifi.sh --aplicar <contrato> "TESTE-ACS-01"
```

`success: true` aqui significa que o SGP falou com o GenieACS. **Nao significa
que a ONU aplicou** - confirme na lista de redes do celular e cronometre. Esse
numero substitui o "alguns minutos" que o bot fala hoje por chute.

Funcionando, religue a opcao no menu:

```bash
cd ~/sgpChat/sgpChat
sed -i '/^WIFI_HABILITADO=/d' .env
docker compose up -d n8n && bash configurar-n8n.sh
```

E teste pelo WhatsApp com o mesmo contrato.

## Derrubar

```bash
cd genieacs && docker compose down -v
```

`-v` apaga o banco junto. A ONU volta ao normal quando o tecnico limpar o
`ManagementServer.URL` - enquanto isso ela so vai tentar falar com um servidor
que nao responde, o que nao atrapalha a navegacao do cliente.
