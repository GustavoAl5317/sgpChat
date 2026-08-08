# Troca de Wi-Fi pelo atendimento automático — o que falta do lado do SGP

**Para:** equipe técnica / responsável pelo SGP
**Assunto:** habilitar a alteração remota de Wi-Fi (nome e senha) dos assinantes

---

## Resumo

O atendimento automático no WhatsApp já está pronto para trocar o nome e a
senha do Wi-Fi do assinante. Ele não consegue executar porque **o SGP não tem
um Gerenciador de CPE cadastrado**, e sem isso não existe caminho até o
roteador do cliente.

Isso não é ajuste no robô: é um componente de infraestrutura que precisa ser
implantado e configurado no SGP e na rede.

Os outros módulos — 2ª via de boleto, diagnóstico de sinal e abertura de
chamado — já funcionam e foram testados com cliente real.

---

## O que foi verificado

**1. A API do SGP recusa toda tentativa.** Chamando `POST /api/ura/cpemanage/`
para o contrato 473:

```
{"msg":"O Serviço de internet não possui Gerenciador de CPE configurado.",
 "success":false}
```

**2. A causa está no próprio SGP.** Em **Sistema → Gerenciador de CPE** a tela
mostra:

```
0 Gerenciadores de CPE
```

Nunca foi cadastrado nenhum. Não é problema daquele contrato — é a base
inteira.

**3. Não há caminho alternativo pela API.** A documentação da API do SGP não
expõe nenhum outro endpoint que escreva configuração no equipamento do
assinante. O `cpemanage` é o único, e ele depende do Gerenciador de CPE.

---

## O que precisa ser feito

São três etapas, nesta ordem. As duas primeiras são do SGP; a terceira é da
rede, e costuma ser a mais trabalhosa.

### Etapa 1 — Ter um servidor ACS (TR-069)

É o servidor que conversa com o roteador dentro da casa do assinante. O SGP não
faz isso sozinho: ele integra com um servidor desses.

O dropdown do SGP de vocês oferece:

| opção | modelo | observação |
|---|---|---|
| **GenieACS (TR-069)** | open source | sem mensalidade; precisa ser hospedado e mantido por vocês |
| **Anlix** | serviço pago | já vem pronto, cobrança por assinante |
| Made4Graph ACS / v2 | — | avaliar |
| ACS Plus | — | avaliar |

A escolha é de vocês. O trabalho de rede (Etapa 3) é o mesmo em qualquer uma.

Se optarem por GenieACS, já temos um ambiente de piloto preparado e podemos
subir para validar com um único equipamento antes de qualquer decisão maior.

### Etapa 2 — Cadastrar o ACS no SGP

Em **Sistema → Gerenciador de CPE → Adicionar Gerenciador de CPE**:

- **Nome:** o gerenciador escolhido
- **Parâmetros JSON:** URL, usuário e senha do servidor

> **Pendência com a TSMX:** o campo "Parâmetros JSON" mostra um exemplo do
> Anlix (`flashman.anlix.io`) e **não muda ao selecionar outro gerenciador no
> dropdown**. Precisamos que a TSMX informe o formato exato esperado para o
> gerenciador que for escolhido.

> **Segunda pendência com a TSMX:** o SGP de vocês é hospedado por eles
> (`rcnet.sgp.tsmx.app`). Quem chama a API do ACS é a nuvem da TSMX, pela
> internet — ou seja, a API precisa ficar exposta publicamente. Precisamos do
> **IP de saída do SGP** para liberar apenas ele no firewall, em vez de deixar
> a API aberta para qualquer origem.

### Etapa 3 — Provisionar as ONUs para o ACS

Esta é a etapa que decide se funciona. Ter o servidor não basta: cada
equipamento na casa do assinante precisa saber o endereço dele.

O que configurar na ONU (normalmente pelo perfil na OLT):

| parâmetro TR-069 | valor |
|---|---|
| `ManagementServer.URL` | endereço do ACS, porta 7547 |
| `ManagementServer.PeriodicInformEnable` | `true` |
| `ManagementServer.PeriodicInformInterval` | conforme a política de vocês |
| `ManagementServer.Username` / `Password` | credencial por equipamento |

Depois disso, no contrato do assinante aparece a aba **Gerenciador de CPE**,
com as ações **Sincronizar** e **Importar WIFI**. Essas duas precisam rodar ao
menos uma vez por contrato — o "Importar WIFI" é o que grava no SGP o nome
atual da rede, informação que hoje vem vazia na API.

### Firewall

| porta | sentido | origem |
|---|---|---|
| 7547 (CWMP) | entrada | rede dos assinantes |
| porta da API do ACS | entrada | apenas o IP do SGP da TSMX |

A API do GenieACS não tem autenticação própria. Se for essa a escolha, ela não
pode ficar exposta sem proxy com TLS e token na frente — já temos essa
configuração pronta.

---

## Como validar

Não é preciso esperar a base inteira. Sugerimos validar com **um equipamento
de funcionário**:

1. Provisionar uma ONU apontando para o ACS
2. No contrato dessa pessoa, rodar **Sincronizar** e depois **Importar WIFI**
3. Usar **Definir Wi-Fi** pela própria interface do SGP e confirmar que a rede
   mudou no local

Se esses três passos funcionarem, o robô funciona — ele usa exatamente essa
mesma chamada. O que fizermos depois é só ligar a opção no menu.

---

## O que fizemos enquanto isso

A opção de Wi-Fi foi ajustada para **abrir um chamado** em vez de tentar
aplicar. O assinante segue o atendimento normalmente — se identifica, escolhe
se quer trocar só o nome, só a senha ou os dois — e ao final é aberta uma
ocorrência no SGP com os dados, para a equipe executar. Ele recebe o número do
protocolo na hora.

Não automatiza, mas o pedido passa a chegar registrado e com identidade já
validada, em vez de por telefone.

> **Ponto a decidir por vocês:** nesse modo, a senha escolhida pelo assinante
> vai no texto da ocorrência, porque o técnico precisa dela para configurar. Ou
> seja, fica registrada no histórico do SGP. Se isso não for aceitável, dá para
> omitir — mas aí o técnico terá que entrar em contato para perguntar, o que
> reduz bastante o ganho.

Quando o Gerenciador de CPE estiver configurado, a mudança do nosso lado é
trocar uma configuração: o mesmo fluxo passa a aplicar sozinho.

---

## Resumo dos pedidos

**Para a TSMX (suporte do SGP):**
1. Qual o formato exato dos "Parâmetros JSON" do Gerenciador de CPE para o
   gerenciador escolhido?
2. Qual o IP de saída do SGP, para liberarmos apenas ele no firewall do ACS?

**Para a equipe da RCNet:**
3. Qual gerenciador de CPE será adotado?
4. As ONUs da base já têm TR-069 habilitado, ou o provisionamento precisa ser
   feito do zero?
5. Podemos validar com o equipamento de um funcionário antes de qualquer
   implantação ampla?
