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
todos os assinantes. Falta aqui: TLS no CWMP (a ONU ainda fala em http),
credencial por equipamento, backup do Mongo, e um dono definido dentro da
RCNet. Piloto que vira producao por inercia e como esse tipo de sistema
costuma vazar - marque uma data para desligar.

## Por que o SGP ser SaaS muda o desenho

O SGP da RCNet roda em `rcnet.sgp.tsmx.app`. Quem chama a API do ACS nao e um
servidor de voces: e a nuvem da TSMX, pela internet. Duas consequencias:

1. A NBI **precisa** estar acessivel publicamente. Nao da para deixar numa
   rede interna, que seria o normal para uma API sem autenticacao.
2. A NBI do GenieACS **nao tem autenticacao nenhuma**. Quem alcanca a porta
   7557 le e escreve na configuracao de todos os roteadores gerenciados.

Por isso a 7557 nao e publicada no compose. Na frente dela fica um nginx na
7558 com TLS e um token no caminho da URL - e, assim que a TSMX informar o IP
de saida do SGP deles, uma allowlist no `nginx.conf.template`. **Peca esse IP
junto com a pergunta do JSON**: e uma linha descomentada que fecha o buraco
inteiro.

Portas e quem fala com cada uma:

| porta | servico | quem acessa |
|-------|---------|-------------|
| 7547  | CWMP    | as ONUs, pela internet |
| 7558  | nginx -> NBI | o SGP (nuvem da TSMX) |
| 3000  | UI      | a equipe - feche no firewall |
| 7557  | NBI crua | ninguem de fora; so o nginx |

## Subir

```bash
cd genieacs
cp .env.example .env
sed -i "s/troque-por-um-valor-aleatorio/$(openssl rand -hex 32)/" .env
sed -i "s/troque-por-um-uuid/$(cat /proc/sys/kernel/random/uuid)/" .env

# Certificado para a NBI. Autoassinado serve para o piloto; se o SGP recusar
# certificado nao confiavel, troque por um do Let's Encrypt num subdominio.
mkdir -p certs
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout certs/acs.key -out certs/acs.crt -subj "/CN=acs"

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

  Pela estrutura do exemplo e pelo padrao que a comunidade usa (nginx na
  frente, https, porta 7558, token no caminho da URL), o formato deve ser:

  ```json
  {"url": "https://SEU-HOST:7558/SEU-TOKEN", "username": "", "password": "",
   "ping_hosts": ["www.google.com", "www.youtube.com"]}
  ```

  O token e o `ACS_TOKEN` do `.env` desta pasta. A URL aponta para o nginx
  (7558), nunca para a NBI crua (7557) nem para o CWMP (7547).

  Se nao funcionar, nao insista adivinhando: pergunte ao suporte da TSMX qual
  JSON o SGP espera para GenieACS. Eles respondem isso em minutos, e cada
  tentativa cega custa uma rodada de teste com o tecnico parado esperando.
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
