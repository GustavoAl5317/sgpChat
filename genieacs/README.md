# GenieACS para a troca de Wi-Fi — implantação

O SGP da RCNet não tem Gerenciador de CPE cadastrado (`0 Gerenciadores de CPE`
em Sistema -> Gerenciador de CPE), e por isso `ura/cpemanage` responde
`O Serviço de internet não possui Gerenciador de CPE configurado` em toda
chamada. Sem um servidor ACS não existe caminho até o roteador do cliente, e
nenhuma mudança no bot resolve isso.

O dropdown do SGP aceita **GenieACS (TR-069)**, que é open source e
self-hosted — sem mensalidade. A TSMX confirmou por escrito que é o mais
comum entre os clientes deles.

Esta implantação roda **na mesma VM do stack do bot** (postgres, redis, n8n,
evolution), em compose e rede Docker separados. Isso isola os containers, mas
não isola o host: um comprometimento da VM em si afeta os dois sistemas
juntos, porque a partir de agora essa máquina tem acesso administrativo ao
roteador de cada assinante da RCNet, além de conversar com os clientes pelo
WhatsApp. Trate o SSH e as atualizações de sistema desta VM com esse peso.

## O que isto entrega, e o que continua fora do nosso controle

**Entrega:** o caminho `bot -> SGP -> GenieACS -> ONU` funcionando, para os
equipamentos que forem provisionados.

**Não entrega sozinho:** cobertura de 100% da base. Isso depende de três
coisas que são trabalho e decisão da RCNet, não do bot:

1. **Provisionar as ONUs** apontando para o ACS — trabalho na OLT.
2. **Suporte a TR-069 no equipamento.** Nem toda ONU tem esse cliente no
   firmware; equipamento mais antigo ou de linha mais simples pode
   simplesmente não suportar, e nesse caso não há configuração que resolva.
3. **ONU em modo bridge com roteador próprio do cliente.** O ACS controlaria
   o Wi-Fi da ONU, que ninguém usa — o roteador do cliente fica fora do
   alcance.

Para os contratos que caírem em (2) ou (3), `WIFI_MODO=chamado` (já
implementado no bot) não é um "enquanto isso" — é a resposta definitiva. Vale
manter os dois modos convivendo: `acs` para quem está provisionado, `chamado`
como rede de segurança para o resto.

## Por que o SGP ser SaaS muda o desenho

O SGP da RCNet roda em `rcnet.sgp.tsmx.app`. Quem chama a API do ACS não é um
servidor de vocês: é a nuvem da TSMX, pela internet. Duas consequências:

1. A NBI **precisa** estar acessível publicamente. Não dá para deixar numa
   rede interna, que seria o normal para uma API sem autenticação.
2. A NBI do GenieACS **não tem autenticação nenhuma**. Quem alcança a porta
   7557 lê e escreve na configuração de **todos** os roteadores gerenciados —
   em produção, isso é a base inteira de assinantes.

Por isso a 7557 não é publicada no compose. Na frente dela fica um nginx na
7558 com TLS e um token no caminho da URL — e, assim que a TSMX informar o IP
de saída do SGP deles, uma allowlist no `nginx.conf.template`. **Peça esse IP
junto com a pergunta do JSON, antes de ir ao ar**: em produção essa linha
descomentada deixa de ser "boa prática" e vira a diferença entre a API estar
protegida por duas camadas ou por uma só.

Portas e quem fala com cada uma:

| porta | serviço | quem acessa |
|-------|---------|-------------|
| 7547  | CWMP    | as ONUs, pela internet |
| 7558  | nginx -> NBI | o SGP (nuvem da TSMX) |
| 3000  | UI      | a equipe — feche no firewall para os IPs de vocês |
| 7557  | NBI crua | ninguém de fora; só o nginx |

## Subir

```bash
cd genieacs
cp .env.example .env
sed -i "s/troque-por-um-valor-aleatorio/$(openssl rand -hex 32)/" .env
sed -i "s/troque-por-um-uuid/$(cat /proc/sys/kernel/random/uuid)/" .env
docker compose up -d --build
docker compose logs -f
```

A interface fica em `http://<ip-do-servidor>:3001` (porta 3000 do host já
estava em uso por outro serviço nesta VM). O primeiro acesso pede para criar o
usuário admin. Feche essa porta no firewall para IPs de fora da equipe assim
que criar o usuário.

Confira que os serviços responderam:

```bash
curl -s -o /dev/null -w 'NBI %{http_code}\n' http://localhost:7557/devices
curl -s -o /dev/null -w 'UI  %{http_code}\n' http://localhost:3000/
```

### Certificado TLS — real, não autoassinado

Em produção o certificado autoassinado não deve ficar para sempre: alguns
clientes TR-069 recusam ou alertam sobre certificado não confiável, e é a
mesma porta que o SGP vai consultar via internet. Use um domínio/subdomínio
apontando para o IP desta VM (ex.: `acs.rcnet.com.br`) e gere com Let's
Encrypt:

```bash
# instala o certbot se ainda nao tiver
sudo apt-get install -y certbot

# a 7558 precisa estar livre por um instante para a validacao HTTP-01;
# pare o nginx deste stack antes
docker compose stop nginx
sudo certbot certonly --standalone -d acs.rcnet.com.br --http-01-port 7558
docker compose cp /etc/letsencrypt/live/acs.rcnet.com.br/fullchain.pem nginx:/etc/nginx/certs/acs.crt
docker compose cp /etc/letsencrypt/live/acs.rcnet.com.br/privkey.pem   nginx:/etc/nginx/certs/acs.key
docker compose start nginx
```

Sem domínio ainda, o autoassinado (`openssl req ...` — veja histórico do
repositório) destrava o teste, mas troque antes de considerar isto
definitivo. Renovação do Let's Encrypt expira a cada 90 dias — agende um cron
para repetir o `certbot renew` e recopiar os arquivos.

### Backup do Mongo — obrigatório, não opcional

```bash
bash backup-mongo.sh
```

Agende diário:

```bash
crontab -e
# adicione:
0 4 * * * cd /caminho/para/genieacs && bash backup-mongo.sh >> backup.log 2>&1
```

Perder este banco não desconfigura o roteador de ninguém, mas o GenieACS
esquece quem gerencia — a troca de Wi-Fi para de funcionar para a base
inteira até cada equipamento reconectar do zero.

## Provisionar as ONUs

Trabalho da equipe de rede da RCNet, na OLT — não é algo que se faz por aqui.
Parâmetro TR-069 a configurar em cada equipamento (ou no perfil aplicado a
eles):

| parâmetro TR-069 | valor |
|---|---|
| `ManagementServer.URL` | `https://<host>:7547/` (confirme se o firmware das ONUs aceita CWMP sobre HTTPS; caso não aceite, HTTP na 7547 é o padrão do GenieACS) |
| `ManagementServer.PeriodicInformEnable` | `true` |
| `ManagementServer.PeriodicInformInterval` | conforme a política de rede de vocês |
| `ManagementServer.Username` / `Password` | credencial por equipamento, se o modelo suportar |

Comandos por fabricante, para o time de rede adaptar ao modelo de OLT. Sao
de referencia da comunidade, **nao verificados contra o manual do equipamento
de voces** — confira antes de virar perfil aplicado em lote:

```
# ZTE C3XX / C6XX  (dentro de pon-onu-mng gpon-onu_1/x/x:y)
tr069-mgmt 1 state unlock
tr069-mgmt 1 acs acs.rcnet.com.br:7547 validate basic username <user> password <senha>
tr069-mgmt 1 tag pri 5 vlan <vlan-de-gerencia>
security-mgmt 999 state enable ingress-type lan protocol tr069

# Huawei MA5600T / MA5800
ont tr069-server-profile add profile-id 1 profile-name "acs"     url "http://acs.rcnet.com.br:7547" user "<user>" "<senha>"
ont tr069-server-config pon 0 all profile-id 1

# Fiberhome AN5000 / AN6000
onu remote-manage-cfg 1 tr069 enable acs-url http://acs.rcnet.com.br:7547     acl-user <user> acl-pswd <senha> inform enable interval 300

# Datacom DM4610
profile gpon tr069-acs-profile ACS
 url http://acs.rcnet.com.br:7547
 username <user>
 password <senha>
onu tr069-acs-profile ACS
```

A ONU precisa alcancar a 7547 pela VLAN de gerencia — se ela nao rotear ate o
ACS, o equipamento nunca aparece em Devices e o sintoma e identico ao de
firmware sem TR-069. Confira o caminho antes de suspeitar do equipamento.

**Mesmo indo direto para a base inteira, comece por um grupo pequeno** —
alguns funcionários ou uma região limitada — antes do rollout completo. Não é
cautela de piloto por cautela: é a primeira vez que este provisionamento roda
nessa rede, e um erro em lote atinge todo mundo ao mesmo tempo. Ver funcionar
em 5-10 equipamentos primeiro é o que permite corrigir engano de configuração
antes que ele alcance um cliente real.

Em poucos minutos cada equipamento provisionado aparece em **Devices** na
interface do GenieACS. Se não aparecer, o problema está no caminho
ONU -> 7547: confira firewall e se a ONU tem TR-069 habilitado no firmware.

## Dois caminhos ate a ONU — escolha um

Depois que o equipamento esta provisionado, ha duas formas de o bot escrever
nele. O trabalho de campo acima e o mesmo nos dois; muda quem chama a NBI.

| | `WIFI_MODO=acs` | `WIFI_MODO=genieacs` |
|---|---|---|
| caminho | bot -> SGP -> NBI -> ONU | bot -> NBI -> ONU |
| Gerenciador de CPE no SGP | obrigatorio | dispensado |
| NBI exposta na internet | **obrigatorio** (o SGP e SaaS) | nao |
| JSON de integracao da TSMX | precisa | nao precisa |
| mapeamento de parametro por modelo | do SGP | **nosso** |
| troca pela interface do SGP / Central | funciona | nao |

O modo `genieacs` existe porque o caminho pelo SGP obriga a publicar na
internet uma API sem autenticacao propria que escreve na configuracao de toda
a base. O proxy da 7558 torna isso aceitavel, mas nao publicar continua sendo
melhor que publicar bem. O preco e o mapeamento de parametro: sem o SGP no
meio, e o bot que precisa saber que parametro cada modelo aceita.

Como ele lida com isso: le o modelo de dados do proprio equipamento e escreve
so o que existe la. `SSID`, e `KeyPassphrase` e/ou `PreSharedKey.1.PreSharedKey`
conforme o modelo expuser (ha firmware que so honra um dos dois). Escolhe a
primeira rede ligada de cada banda quando o firmware informa a banda; todas as
ligadas quando nao informa.

E, principalmente, **recusa em vez de chutar**. Nao aplica nada quando:

| motivo no log | o que significa |
|---|---|
| `device_nao_encontrado` | ONU nao provisionada no ACS — esperado durante o rollout |
| `device_ambiguo` | login e MAC casaram com equipamentos diferentes |
| `sem_wlanconfiguration` | modelo TR-181, ou arvore nunca lida pelo ACS |
| `senha_nao_escrivel` / `ssid_nao_escrivel` | mapeamento do modelo precisa ser resolvido |

Em todos, o assinante ouve que a rede continua como estava e vai para o
atendente. O motivo fica em `wa_wifi_change_log` — e ele que diz o que
corrigir no provisionamento. Ver essas recusas no comeco e o desenho
funcionando, nao falha.

### Ligar o modo direto

```bash
cd ~/sgpChat/sgpChat
sed -i 's/^WIFI_MODO=.*/WIFI_MODO=genieacs/' .env
docker compose up -d          # cria a rede acs_nbi e religa o n8n nela
cd genieacs && docker compose up -d   # poe o GenieACS na mesma rede
cd .. && bash configurar-n8n.sh
```

A ordem importa: a rede `acs_nbi` e criada pelo compose do bot e consumida
como externa pelo do GenieACS. Confira que o n8n enxerga a NBI:

```bash
docker exec botsgp-n8n wget -qO- http://genieacs:7557/devices/?query=%7B%7D | head -c 200
```

Depois de provisionar a primeira ONU, veja como o PPPoE aparece nela e fixe o
caminho em `GENIEACS_LOGIN_PARAM`:

```bash
docker exec botsgp-n8n wget -qO- 'http://genieacs:7557/devices/' | grep -o '[A-Za-z.0-9]*Username'
```

## Cadastrar no SGP

Necessario apenas para `WIFI_MODO=acs` (e para a troca pela interface do SGP
e pela Central do Assinante). No modo `genieacs` isto e opcional — da para
cadastrar depois, sem parar o atendimento.

Sistema -> Gerenciador de CPE -> Adicionar:

- **Nome:** GenieACS (TR-069)
- **Descrição:** algo que identifique o ambiente
- **Parâmetros JSON:** aqui há uma incerteza que vale resolver antes de ir ao
  ar. O campo mostra um exemplo com `flashman.anlix.io` e **ele não muda ao
  escolher GenieACS no dropdown** — é placeholder estático, não template por
  gerenciador.

  Pela estrutura do exemplo e pelo padrão que a comunidade usa (nginx na
  frente, https, porta 7558, token no caminho da URL), o formato deve ser:

  ```json
  {"url": "https://SEU-HOST:7558/SEU-TOKEN", "username": "", "password": "",
   "ping_hosts": ["www.google.com", "www.youtube.com"]}
  ```

  O token é o `ACS_TOKEN` do `.env` desta pasta. A URL aponta para o nginx
  (7558), nunca para a NBI crua (7557) nem para o CWMP (7547).

  **Não insista adivinhando** — pergunte ao suporte da TSMX qual JSON o SGP
  espera para GenieACS, e o IP de saída deles para a allowlist. Em produção,
  cada tentativa cega arrisca escrever algo errado na configuração de um
  equipamento real.
- Marque **Central definir Wifi** se quiser que a Central do Assinante também
  ofereça a troca.

## Testar antes de expor ao cliente

```bash
cd ~/sgpChat/sgpChat
bash testar-wifi.sh --cpf <cpf-de-um-contrato-provisionado>
bash testar-wifi.sh --aplicar <contrato> "<nome-que-a-rede-ja-tinha>"
```

`success: true` aqui significa que o SGP falou com o GenieACS. **Não
significa que a ONU aplicou** — confirme na lista de redes do celular do
dono desse contrato e cronometre. Esse número substitui o "alguns minutos"
que o bot fala hoje por chute.

Só depois disso, ligue o modo automático:

```bash
cd ~/sgpChat/sgpChat
sed -i 's/^WIFI_MODO=.*/WIFI_MODO=acs/' .env
docker compose up -d n8n && bash configurar-n8n.sh
```

E teste pelo WhatsApp com um contrato provisionado.

## Se precisar reverter

```bash
cd ~/sgpChat/sgpChat
sed -i 's/^WIFI_MODO=.*/WIFI_MODO=chamado/' .env
docker compose up -d n8n && bash configurar-n8n.sh
```

Isso tira o bot de aplicar direto e volta a abrir chamado para a equipe — sem
mexer no GenieACS nem nas ONUs já provisionadas.
